package com.hanai.core.fixtures

/**
 * 테스트 전용 최소 JSON 파서. 외부 의존성을 피하기 위해 직접 구현했다.
 * 숫자는 모두 Double, 객체는 Map, 배열은 List로 읽는다.
 */
internal typealias JsonObject = Map<String, Any?>

internal object MiniJson {
    fun parse(text: String): Any? {
        val parser = Parser(text)
        val value = parser.readValue()
        parser.skipWhitespace()
        require(parser.index == text.length) { "JSON에 남은 문자가 있습니다: index ${parser.index}" }
        return value
    }

    private class Parser(private val text: String) {
        var index = 0

        fun skipWhitespace() {
            while (index < text.length && text[index].isWhitespace()) index++
        }

        fun readValue(): Any? {
            skipWhitespace()
            require(index < text.length) { "JSON이 갑자기 끝났습니다" }
            val c = text[index]
            return when {
                c == '{' -> readObject()
                c == '[' -> readArray()
                c == '"' -> readString()
                c == 't' -> readLiteral("true", true)
                c == 'f' -> readLiteral("false", false)
                c == 'n' -> readLiteral("null", null)
                c == '-' || c.isDigit() -> readNumber()
                else -> error("예상하지 못한 문자 '$c' (index $index)")
            }
        }

        private fun readLiteral(word: String, value: Any?): Any? {
            require(text.startsWith(word, index)) { "리터럴 오류 (index $index)" }
            index += word.length
            return value
        }

        private fun readNumber(): Double {
            val start = index
            if (text[index] == '-') index++
            while (index < text.length && (text[index].isDigit() || text[index] in "+-.eE")) index++
            return text.substring(start, index).toDouble()
        }

        private fun readString(): String {
            index++ // 여는 따옴표
            val builder = StringBuilder()
            while (true) {
                require(index < text.length) { "문자열이 닫히지 않았습니다" }
                val c = text[index++]
                if (c == '"') return builder.toString()
                if (c != '\\') {
                    builder.append(c)
                    continue
                }
                val escaped = text[index++]
                when (escaped) {
                    '"' -> builder.append('"')
                    '\\' -> builder.append('\\')
                    '/' -> builder.append('/')
                    'b' -> builder.append('\b')
                    'f' -> builder.append('')
                    'n' -> builder.append('\n')
                    'r' -> builder.append('\r')
                    't' -> builder.append('\t')
                    'u' -> {
                        builder.append(text.substring(index, index + 4).toInt(16).toChar())
                        index += 4
                    }
                    else -> error("잘못된 이스케이프 '\\$escaped' (index ${index - 1})")
                }
            }
        }

        private fun readArray(): List<Any?> {
            index++ // [
            val list = mutableListOf<Any?>()
            skipWhitespace()
            if (text[index] == ']') {
                index++
                return list
            }
            while (true) {
                list.add(readValue())
                skipWhitespace()
                val c = text[index++]
                if (c == ']') return list
                require(c == ',') { "배열 구분자 오류 (index ${index - 1})" }
            }
        }

        private fun readObject(): JsonObject {
            index++ // {
            val map = LinkedHashMap<String, Any?>()
            skipWhitespace()
            if (text[index] == '}') {
                index++
                return map
            }
            while (true) {
                skipWhitespace()
                require(text[index] == '"') { "객체 키는 문자열이어야 합니다 (index $index)" }
                val key = readString()
                skipWhitespace()
                require(text[index++] == ':') { "':'가 필요합니다 (index ${index - 1})" }
                map[key] = readValue()
                skipWhitespace()
                val c = text[index++]
                if (c == '}') return map
                require(c == ',') { "객체 구분자 오류 (index ${index - 1})" }
            }
        }
    }
}

@Suppress("UNCHECKED_CAST")
internal fun JsonObject.objOrNull(key: String): JsonObject? = this[key] as? Map<String, Any?>

internal fun JsonObject.obj(key: String): JsonObject =
    objOrNull(key) ?: error("'$key' 객체가 없습니다")

@Suppress("UNCHECKED_CAST")
internal fun JsonObject.objects(key: String): List<JsonObject> =
    (this[key] as? List<Any?>)?.map { it as Map<String, Any?> } ?: error("'$key' 배열이 없습니다")

internal fun JsonObject.doubleOrNull(key: String): Double? = this[key] as? Double

internal fun JsonObject.double(key: String): Double =
    doubleOrNull(key) ?: error("'$key' 숫자가 없습니다")

internal fun JsonObject.doubles(key: String): List<Double> =
    (this[key] as? List<*>)?.map { it as Double } ?: emptyList()

internal fun JsonObject.intOrNull(key: String): Int? = doubleOrNull(key)?.toInt()

internal fun JsonObject.int(key: String): Int = double(key).toInt()

internal fun JsonObject.stringOrNull(key: String): String? = this[key] as? String

internal fun JsonObject.string(key: String): String =
    stringOrNull(key) ?: error("'$key' 문자열이 없습니다")

internal fun JsonObject.boolOrNull(key: String): Boolean? = this[key] as? Boolean

internal fun JsonObject.bool(key: String): Boolean =
    boolOrNull(key) ?: error("'$key' 불리언이 없습니다")
