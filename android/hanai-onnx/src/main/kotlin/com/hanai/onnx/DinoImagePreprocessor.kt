package com.hanai.onnx

import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/**
 * Minimal direct port of publicly verified Pillow 12.3 RGB8 resampling and Transformers 5.20 normalization.
 * Reference public sources:
 * - Pillow (MIT-CMU commit bb1d8e8ab8d29048624d96e3ee53cecf7c13d13d: src/libImaging/Resample.c and src/PIL/Image.py)
 * - Transformers (Apache 2.0 commit 0ea877911a11499053df677befd4b35b172ff502: image_transforms.py)
 */
object DinoImagePreprocessor {

    private const val MAX_DIMENSION = 16384
    private const val MAX_PIXELS = 16_000_000L
    private const val TARGET_SHORT_EDGE = 256
    private const val CROP_SIZE = 224
    private const val TOTAL_FLOATS = 1 * 3 * CROP_SIZE * CROP_SIZE // 150528
    private const val CROP_PIXELS = CROP_SIZE * CROP_SIZE

    private const val BICUBIC_A = -0.5
    private const val Q22 = 4194304.0
    private const val Q22_ROUND = 2097152L
    private const val SCALE_FACTOR_DOUBLE = 0.00392156862745098

    private val MEAN = floatArrayOf(0.485f, 0.456f, 0.406f)
    private val STD = floatArrayOf(0.229f, 0.224f, 0.225f)

    private class ResampleCoefficients(
        val xmin: Int,
        val k: IntArray
    )

    @JvmStatic
    fun normalizedInput(rgb: ByteArray, width: Int, height: Int): FloatArray {
        if (width <= 0 || width > MAX_DIMENSION) {
            throw IllegalArgumentException("Invalid image width: must be > 0 and <= $MAX_DIMENSION")
        }
        if (height <= 0 || height > MAX_DIMENSION) {
            throw IllegalArgumentException("Invalid image height: must be > 0 and <= $MAX_DIMENSION")
        }
        val pixelCount = width.toLong() * height.toLong()
        if (pixelCount > MAX_PIXELS) {
            throw IllegalArgumentException("Image pixel count exceeds maximum allowed limit")
        }
        val expectedBytes = pixelCount * 3L
        if (rgb.size.toLong() != expectedBytes) {
            throw IllegalArgumentException("Input byte array size does not match width * height * 3")
        }

        val resizedWidth: Int
        val resizedHeight: Int
        if (width <= height) {
            resizedWidth = TARGET_SHORT_EDGE
            resizedHeight = floor(256.0 * height.toDouble() / width.toDouble()).toInt()
        } else {
            resizedHeight = TARGET_SHORT_EDGE
            resizedWidth = floor(256.0 * width.toDouble() / height.toDouble()).toInt()
        }

        val xCropOffset = floor((resizedWidth - CROP_SIZE).toDouble() / 2.0).toInt()
        val yCropOffset = floor((resizedHeight - CROP_SIZE).toDouble() / 2.0).toInt()

        val extremeTall = (height.toLong() > 100L * width.toLong()) && (resizedHeight < height)

        val croppedRgb = if (extremeTall) {
            // Vertical first: intermediate is 224 x sourceWidth RGB8, then horizontal second to 224x224
            val vCoeffs = computeAxisCoefficients(height, resizedHeight, yCropOffset, CROP_SIZE)
            val intermediate = resampleVertical(rgb, width, vCoeffs)
            val hCoeffs = computeAxisCoefficients(width, resizedWidth, xCropOffset, CROP_SIZE)
            resampleHorizontal(intermediate, width, CROP_SIZE, hCoeffs)
        } else {
            // Horizontal first: intermediate is sourceHeight x 224 RGB8, then vertical second to 224x224
            val hCoeffs = computeAxisCoefficients(width, resizedWidth, xCropOffset, CROP_SIZE)
            val intermediate = resampleHorizontal(rgb, width, height, hCoeffs)
            val vCoeffs = computeAxisCoefficients(height, resizedHeight, yCropOffset, CROP_SIZE)
            resampleVertical(intermediate, CROP_SIZE, vCoeffs)
        }

        // Convert cropped unsigned RGB to NCHW FloatArray (1 x 3 x 224 x 224)
        val result = FloatArray(TOTAL_FLOATS)
        val planeG = CROP_PIXELS
        val planeB = 2 * CROP_PIXELS
        val meanR = MEAN[0]; val meanG = MEAN[1]; val meanB = MEAN[2]
        val stdR = STD[0]; val stdG = STD[1]; val stdB = STD[2]

        var srcByteOffset = 0
        for (i in 0 until CROP_PIXELS) {
            val rUnsigned = croppedRgb[srcByteOffset].toInt() and 0xFF
            val gUnsigned = croppedRgb[srcByteOffset + 1].toInt() and 0xFF
            val bUnsigned = croppedRgb[srcByteOffset + 2].toInt() and 0xFF
            srcByteOffset += 3

            val rScaled = (rUnsigned.toDouble() * SCALE_FACTOR_DOUBLE).toFloat()
            val gScaled = (gUnsigned.toDouble() * SCALE_FACTOR_DOUBLE).toFloat()
            val bScaled = (bUnsigned.toDouble() * SCALE_FACTOR_DOUBLE).toFloat()

            result[i] = (rScaled - meanR) / stdR
            result[planeG + i] = (gScaled - meanG) / stdG
            result[planeB + i] = (bScaled - meanB) / stdB
        }
        return result
    }

    private fun resampleHorizontal(
        src: ByteArray,
        srcWidth: Int,
        rowCount: Int,
        hCoeffs: Array<ResampleCoefficients>
    ): ByteArray {
        val out = ByteArray(rowCount * CROP_SIZE * 3)
        for (y in 0 until rowCount) {
            val inRowOffset = y * srcWidth * 3
            val outRowOffset = y * CROP_SIZE * 3
            for (x in 0 until CROP_SIZE) {
                val coeff = hCoeffs[x]
                val kWeights = coeff.k
                val tapCount = kWeights.size
                var accR = Q22_ROUND
                var accG = Q22_ROUND
                var accB = Q22_ROUND

                var srcOffset = inRowOffset + coeff.xmin * 3
                for (tap in 0 until tapCount) {
                    val kw = kWeights[tap].toLong()
                    accR += (src[srcOffset].toInt() and 0xFF) * kw
                    accG += (src[srcOffset + 1].toInt() and 0xFF) * kw
                    accB += (src[srcOffset + 2].toInt() and 0xFF) * kw
                    srcOffset += 3
                }

                val outOffset = outRowOffset + x * 3
                out[outOffset] = clampToByte(accR shr 22)
                out[outOffset + 1] = clampToByte(accG shr 22)
                out[outOffset + 2] = clampToByte(accB shr 22)
            }
        }
        return out
    }

    private fun resampleVertical(
        src: ByteArray,
        colCount: Int,
        vCoeffs: Array<ResampleCoefficients>
    ): ByteArray {
        val out = ByteArray(CROP_SIZE * colCount * 3)
        val stride = colCount * 3
        for (y in 0 until CROP_SIZE) {
            val coeff = vCoeffs[y]
            val ymin = coeff.xmin
            val kWeights = coeff.k
            val tapCount = kWeights.size
            val outRowOffset = y * stride

            for (x in 0 until colCount) {
                var accR = Q22_ROUND
                var accG = Q22_ROUND
                var accB = Q22_ROUND

                val colByteOffset = x * 3
                var srcOffset = ymin * stride + colByteOffset
                for (tap in 0 until tapCount) {
                    val kw = kWeights[tap].toLong()
                    accR += (src[srcOffset].toInt() and 0xFF) * kw
                    accG += (src[srcOffset + 1].toInt() and 0xFF) * kw
                    accB += (src[srcOffset + 2].toInt() and 0xFF) * kw
                    srcOffset += stride
                }

                val outPixelOffset = outRowOffset + colByteOffset
                out[outPixelOffset] = clampToByte(accR shr 22)
                out[outPixelOffset + 1] = clampToByte(accG shr 22)
                out[outPixelOffset + 2] = clampToByte(accB shr 22)
            }
        }
        return out
    }

    private fun bicubicKernel(x: Double): Double {
        val t = abs(x)
        return if (t < 1.0) {
            ((BICUBIC_A + 2.0) * t - (BICUBIC_A + 3.0)) * t * t + 1.0
        } else if (t < 2.0) {
            (((t - 5.0) * t + 8.0) * t - 4.0) * BICUBIC_A
        } else {
            0.0
        }
    }

    private fun computeAxisCoefficients(
        srcSize: Int,
        dstSize: Int,
        cropOffset: Int,
        cropCount: Int
    ): Array<ResampleCoefficients> {
        return Array(cropCount) { i ->
            val j = cropOffset + i
            if (srcSize == dstSize) {
                ResampleCoefficients(xmin = j, k = intArrayOf(4194304))
            } else {
                val scale = srcSize.toDouble() / dstSize.toDouble()
                val filterscale = max(scale, 1.0)
                val support = 2.0 * filterscale
                val invFilterscale = 1.0 / filterscale
                val center = (j.toDouble() + 0.5) * scale

                val xmin = max(0, (center - support + 0.5).toInt())
                val xmax = min(srcSize, (center + support + 0.5).toInt())

                val tapCount = xmax - xmin
                val rawWeights = DoubleArray(tapCount)
                var rawSum = 0.0

                for (tap in 0 until tapCount) {
                    val rawArg = (tap.toDouble() + xmin.toDouble() - center + 0.5) * invFilterscale
                    val weight = bicubicKernel(rawArg)
                    rawWeights[tap] = weight
                    rawSum += weight
                }

                val kArray = IntArray(tapCount)
                for (tap in 0 until tapCount) {
                    val normalizedW = if (rawSum != 0.0) rawWeights[tap] / rawSum else 0.0
                    val kVal = if (normalizedW < 0.0) {
                        (-0.5 + normalizedW * Q22).toInt()
                    } else {
                        (0.5 + normalizedW * Q22).toInt()
                    }
                    kArray[tap] = kVal
                }

                ResampleCoefficients(xmin = xmin, k = kArray)
            }
        }
    }

    private fun clampToByte(v: Long): Byte {
        val clamped = when {
            v < 0L -> 0
            v > 255L -> 255
            else -> v.toInt()
        }
        return clamped.toByte()
    }
}
