package com.hanai.onnx

import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import java.io.File
import java.io.FileInputStream
import java.nio.FloatBuffer
import java.security.MessageDigest
import kotlin.math.sqrt

class DinoImageFeatures private constructor(
    private val session: OrtSession,
    private val sessionOptions: OrtSession.SessionOptions
) : AutoCloseable {

    private var closed = false

    fun infer(preprocessedFloatArray: FloatArray): FloatArray {
        if (preprocessedFloatArray.size != EXPECTED_INPUT_SIZE) {
            throw IllegalArgumentException("Input float array size mismatch: expected $EXPECTED_INPUT_SIZE")
        }

        // Snapshot input array once before validation and reuse the same snapshot for tensor buffer creation
        // to prevent caller mutation between finite/range validation and native parsing.
        val inputSnapshot = preprocessedFloatArray.copyOf()

        // Validate finite and per-channel ImageNet-normalized RGB range: [-mean/std, (1-mean)/std] with tiny roundoff
        for (i in 0 until EXPECTED_INPUT_SIZE) {
            val v = inputSnapshot[i]
            if (!v.isFinite()) {
                throw IllegalArgumentException("Input float array contains non-finite value at index $i")
            }
            val channel = i / (IMAGE_HEIGHT * IMAGE_WIDTH)
            val minBound = CHANNEL_MIN_BOUNDS[channel]
            val maxBound = CHANNEL_MAX_BOUNDS[channel]
            if (v < minBound || v > maxBound) {
                throw IllegalArgumentException("Input value at index $i out of ImageNet normalized range for channel $channel")
            }
        }

        synchronized(this) {
            if (closed) {
                throw IllegalStateException("DinoImageFeatures is closed")
            }
            if (Thread.currentThread().isInterrupted) {
                throw InterruptedException("Thread interrupted before ONNX inference")
            }

            var tensor: OnnxTensor? = null
            var result: OrtSession.Result? = null
            var malformedNativeFailure = false

            try {
                val env = OrtEnvironment.getEnvironment()
                val buffer = FloatBuffer.wrap(inputSnapshot)
                tensor = OnnxTensor.createTensor(env, buffer, INPUT_SHAPE)

                val inputs = mapOf(INPUT_NAME to tensor)
                result = session.run(inputs)

                if (Thread.currentThread().isInterrupted) {
                    throw InterruptedException("Thread interrupted after ONNX inference")
                }

                val outputObj = result.get(0).value
                val rawOutput: Array<FloatArray> = try {
                    @Suppress("UNCHECKED_CAST")
                    outputObj as Array<FloatArray>
                } catch (e: Exception) {
                    malformedNativeFailure = true
                    throw IllegalStateException("Malformed ONNX output structure", e)
                }

                if (rawOutput.size != 1 || rawOutput[0].size != EXPECTED_OUTPUT_SIZE) {
                    malformedNativeFailure = true
                    throw IllegalStateException("Malformed ONNX output embedding dimensions")
                }

                val embedding = rawOutput[0]
                var sumSq = 0.0
                for (i in 0 until EXPECTED_OUTPUT_SIZE) {
                    val v = embedding[i]
                    if (!v.isFinite()) {
                        malformedNativeFailure = true
                        throw IllegalStateException("Malformed ONNX output contains non-finite value at index $i")
                    }
                    sumSq += v.toDouble() * v.toDouble()
                }

                val norm = sqrt(sumSq)
                if (!norm.isFinite() || norm <= 0.0) {
                    malformedNativeFailure = true
                    throw IllegalStateException("Invalid output embedding norm: zero or non-finite")
                }

                val normalized = FloatArray(EXPECTED_OUTPUT_SIZE)
                var normSumSq = 0.0
                for (i in 0 until EXPECTED_OUTPUT_SIZE) {
                    val n = (embedding[i].toDouble() / norm).toFloat()
                    if (!n.isFinite() || n < -1.0f - EPSILON || n > 1.0f + EPSILON) {
                        malformedNativeFailure = true
                        throw IllegalStateException("Normalized embedding value out of [-1, 1] range at index $i")
                    }
                    normalized[i] = n.coerceIn(-1.0f, 1.0f)
                    normSumSq += normalized[i].toDouble() * normalized[i].toDouble()
                }

                val finalNorm = sqrt(normSumSq)
                if (finalNorm < 1.0 - UNIT_NORM_TOL || finalNorm > 1.0 + UNIT_NORM_TOL) {
                    malformedNativeFailure = true
                    throw IllegalStateException("Normalized embedding unit norm check failed")
                }

                return normalized
            } catch (e: Throwable) {
                if (e is InterruptedException) {
                    throw e
                }
                if (malformedNativeFailure || e is ai.onnxruntime.OrtException) {
                    internalClose()
                }
                throw e
            } finally {
                try {
                    result?.close()
                } catch (_: Throwable) {}
                try {
                    tensor?.close()
                } catch (_: Throwable) {}
            }
        }
    }

    override fun close() {
        synchronized(this) {
            internalClose()
        }
    }

    private fun internalClose() {
        if (!closed) {
            closed = true
            try {
                session.close()
            } catch (_: Throwable) {}
            try {
                sessionOptions.close()
            } catch (_: Throwable) {}
        }
    }

    companion object {
        private const val EXPECTED_MODEL_SIZE = 88400292L
        private const val EXPECTED_SHA256 = "6b3e37fdea1393dda0ea0312f5f19b20829ca96447ab5b8b3c92a36777fc431d"

        private const val INPUT_NAME = "pixel_values"
        private const val OUTPUT_NAME = "embedding"

        private const val CHANNELS = 3
        private const val IMAGE_HEIGHT = 224
        private const val IMAGE_WIDTH = 224
        const val EXPECTED_INPUT_SIZE = CHANNELS * IMAGE_HEIGHT * IMAGE_WIDTH // 150528
        const val EXPECTED_OUTPUT_SIZE = 384

        private val INPUT_SHAPE = longArrayOf(1L, 3L, 224L, 224L)

        private const val EPSILON = 1e-4f
        private const val UNIT_NORM_TOL = 1e-3

        // ImageNet means: [0.485, 0.456, 0.406], stds: [0.229, 0.224, 0.225]
        // Normalized bounds: [-mean / std, (1.0 - mean) / std] with EPSILON roundoff allowance
        private val CHANNEL_MIN_BOUNDS = floatArrayOf(
            (-0.485f / 0.229f) - EPSILON,
            (-0.456f / 0.224f) - EPSILON,
            (-0.406f / 0.225f) - EPSILON
        )
        private val CHANNEL_MAX_BOUNDS = floatArrayOf(
            ((1.0f - 0.485f) / 0.229f) + EPSILON,
            ((1.0f - 0.456f) / 0.224f) + EPSILON,
            ((1.0f - 0.406f) / 0.225f) + EPSILON
        )

        @JvmStatic
        fun open(modelFile: File): DinoImageFeatures {
            val path = modelFile.toPath()

            if (java.nio.file.Files.isSymbolicLink(path)) {
                throw IllegalArgumentException("Model file cannot be a symbolic link")
            }
            if (!modelFile.exists() || !modelFile.isFile) {
                throw IllegalArgumentException("Model file does not exist or is not a regular file")
            }

            // Path component symlink check
            var currentPath: java.nio.file.Path? = path.toAbsolutePath().normalize()
            while (currentPath != null) {
                if (java.nio.file.Files.isSymbolicLink(currentPath)) {
                    throw IllegalArgumentException("Model file path component cannot be a symbolic link")
                }
                currentPath = currentPath.parent
            }

            val fileSize = modelFile.length()
            if (fileSize != EXPECTED_MODEL_SIZE) {
                throw IllegalArgumentException("Model file size mismatch: expected $EXPECTED_MODEL_SIZE bytes, got $fileSize")
            }

            val modelBytes = ByteArray(EXPECTED_MODEL_SIZE.toInt())
            val digest = MessageDigest.getInstance("SHA-256")

            FileInputStream(modelFile).use { fis ->
                var totalBytesRead = 0
                val chunkSize = 65536
                while (totalBytesRead < modelBytes.size) {
                    if (Thread.currentThread().isInterrupted) {
                        throw InterruptedException("Thread interrupted while streaming model file")
                    }
                    val toRead = minOf(chunkSize, modelBytes.size - totalBytesRead)
                    val read = fis.read(modelBytes, totalBytesRead, toRead)
                    if (read == -1) {
                        throw IllegalStateException("Unexpected EOF before reading full model content")
                    }
                    digest.update(modelBytes, totalBytesRead, read)
                    totalBytesRead += read
                }

                if (fis.read() != -1) {
                    throw IllegalStateException("Model file contains trailing bytes beyond expected size")
                }
            }

            val actualHash = digest.digest().joinToString("") { "%02x".format(it) }
            if (!actualHash.equals(EXPECTED_SHA256, ignoreCase = true)) {
                throw SecurityException("Model SHA-256 mismatch")
            }

            if (Thread.currentThread().isInterrupted) {
                throw InterruptedException("Thread interrupted before creating ONNX session")
            }

            val env = OrtEnvironment.getEnvironment()
            // Explicitly disable telemetry on the shared OrtEnvironment singleton before creating the session.
            // Note: This changes telemetry for any other ORT users sharing this environment singleton within the same process.
            env.setTelemetry(false)

            val sessionOptions = OrtSession.SessionOptions().apply {
                setIntraOpNumThreads(2)
                setInterOpNumThreads(1)
                setExecutionMode(OrtSession.SessionOptions.ExecutionMode.SEQUENTIAL)
                setOptimizationLevel(OrtSession.SessionOptions.OptLevel.BASIC_OPT)
            }

            var session: OrtSession? = null
            try {
                session = env.createSession(modelBytes, sessionOptions)

                // Validate input metadata
                val inputInfoMap = session.inputInfo
                if (inputInfoMap.size != 1 || !inputInfoMap.containsKey(INPUT_NAME)) {
                    throw IllegalStateException("Model must have exactly one input named $INPUT_NAME")
                }
                val inputNodeInfo = inputInfoMap[INPUT_NAME]
                    ?: throw IllegalStateException("Missing input metadata for $INPUT_NAME")
                val inputTensorInfo = inputNodeInfo.info as? TensorInfo
                    ?: throw IllegalStateException("Input is not a TensorInfo")
                if (inputTensorInfo.type != OnnxJavaType.FLOAT) {
                    throw IllegalStateException("Input tensor type must be FLOAT")
                }
                val inputShape = inputTensorInfo.shape
                if (!inputShape.contentEquals(INPUT_SHAPE)) {
                    throw IllegalStateException("Input tensor shape mismatch: expected [1, 3, 224, 224]")
                }

                // Validate output metadata
                val outputInfoMap = session.outputInfo
                if (outputInfoMap.size != 1 || !outputInfoMap.containsKey(OUTPUT_NAME)) {
                    throw IllegalStateException("Model must have exactly one output named $OUTPUT_NAME")
                }
                val outputNodeInfo = outputInfoMap[OUTPUT_NAME]
                    ?: throw IllegalStateException("Missing output metadata for $OUTPUT_NAME")
                val outputTensorInfo = outputNodeInfo.info as? TensorInfo
                    ?: throw IllegalStateException("Output is not a TensorInfo")
                if (outputTensorInfo.type != OnnxJavaType.FLOAT) {
                    throw IllegalStateException("Output tensor type must be FLOAT")
                }
                val outputShape = outputTensorInfo.shape
                val expectedOutputShape = longArrayOf(1L, EXPECTED_OUTPUT_SIZE.toLong())
                if (!outputShape.contentEquals(expectedOutputShape)) {
                    throw IllegalStateException("Output tensor shape mismatch: expected [1, 384]")
                }

                return DinoImageFeatures(session, sessionOptions)
            } catch (e: Throwable) {
                try {
                    session?.close()
                } catch (_: Throwable) {}
                try {
                    sessionOptions.close()
                } catch (_: Throwable) {}
                throw e
            }
        }
    }
}
