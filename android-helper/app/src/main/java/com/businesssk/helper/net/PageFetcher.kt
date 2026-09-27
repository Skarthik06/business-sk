package com.businesssk.helper.net

import okhttp3.Call
import okhttp3.Dns
import okhttp3.EventListener
import okhttp3.Headers
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * Fetches ONE shopping page the way the laptop worker does, with the phone's own safety rules:
 *  - https only, and only sites on the phone's allow-list (checked on every redirect hop)
 *  - hosts that resolve to private / local addresses are refused (the server can never make the
 *    phone reach your home network)
 *  - compressed download (OkHttp asks for gzip) and every network byte is counted
 *  - a tiny / cut-off page (a momentary throttle) is retried with a growing pause inside the job's time
 */
class PageFetcher(private val allowed: Collection<String>) {

    data class Page(
        val status: Int?,
        val body: ByteArray,
        val finalUrl: String,
        val contentType: String,
        val error: String?,
        val ms: Long,
    )

    /** Network bytes downloaded by this fetcher (compressed, as billed by the carrier). */
    val networkBytes = AtomicLong(0)

    private val safeDns = object : Dns {
        override fun lookup(hostname: String): List<InetAddress> {
            val all = Dns.SYSTEM.lookup(hostname)
            if (all.isEmpty() || all.any { !isPublic(it) }) {
                throw UnknownHostException("refused: $hostname resolves to a private/local address")
            }
            return all
        }
    }

    private val client: OkHttpClient = OkHttpClient.Builder()
        .dns(safeDns)
        .followRedirects(false)                   // redirects are followed by hand so each hop is checked
        .followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(40, TimeUnit.SECONDS)
        .eventListener(object : EventListener() {
            override fun responseBodyEnd(call: Call, byteCount: Long) {
                networkBytes.addAndGet(byteCount)
            }
        })
        .build()

    fun hostAllowed(host: String): Boolean {
        val h = host.lowercase().trimEnd('.')
        return allowed.any { d -> h == d || h.endsWith(".$d") }
    }

    fun fetch(url: String, timeoutSec: Int): Page {
        val start = System.currentTimeMillis()
        val deadline = start + maxOf(10, timeoutSec) * 1000L
        var page = Page(null, ByteArray(0), url, "", "not attempted", 0)
        for (attempt in 1..3) {
            val left = deadline - System.currentTimeMillis()
            if (left < 5_000) break
            page = fetchOnce(url, left)
            val pause = (4L * attempt + 1) * 1000L                     // 5 s, 9 s
            val throttled = page.status == 200 && page.body.size < 20_000
            if (throttled && attempt < 3 && deadline - System.currentTimeMillis() > pause + 8_000) {
                Thread.sleep(pause)
                continue
            }
            break
        }
        return page.copy(ms = System.currentTimeMillis() - start)
    }

    private fun fetchOnce(url: String, timeoutMs: Long): Page {
        var current: HttpUrl = url.toHttpUrlOrNull() ?: return fail(url, "invalid URL")
        repeat(MAX_REDIRECTS + 1) {
            if (current.scheme != "https") return fail(current.toString(), "only https is allowed")
            if (!hostAllowed(current.host)) return fail(current.toString(), "site not allowed on this phone: ${current.host}")
            val call = client.newCall(Request.Builder().url(current).headers(BROWSER).build())
            call.timeout().timeout(timeoutMs, TimeUnit.MILLISECONDS)
            try {
                call.execute().use { resp ->
                    if (resp.isRedirect) {
                        val next = resp.header("Location")?.let { current.resolve(it) }
                            ?: return fail(current.toString(), "bad redirect")
                        current = next
                        return@repeat
                    }
                    val source = resp.body?.source() ?: return Page(resp.code, ByteArray(0), current.toString(),
                        resp.header("Content-Type").orEmpty(), null, 0)
                    source.request(MAX_BYTES)
                    val bytes = source.buffer.readByteArray(minOf(source.buffer.size, MAX_BYTES))
                    return Page(resp.code, bytes, current.toString(), resp.header("Content-Type").orEmpty(), null, 0)
                }
            } catch (e: Exception) {
                return fail(current.toString(), "${e.javaClass.simpleName}: ${e.message?.take(120)}")
            }
        }
        return fail(current.toString(), "too many redirects")
    }

    private fun fail(url: String, why: String) = Page(null, ByteArray(0), url, "", why, 0)

    companion object {
        private const val MAX_REDIRECTS = 5
        private const val MAX_BYTES = 8_000_000L

        // Desktop Chrome headers (the server's parsers expect the desktop page). No Accept-Encoding:
        // OkHttp adds gzip itself and decodes it transparently.
        private val BROWSER: Headers = Headers.Builder()
            .add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
            .add("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
            .add("Accept-Language", "en-IN,en-GB;q=0.9,en;q=0.8")
            .add("sec-ch-ua", "\"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\"")
            .add("sec-ch-ua-mobile", "?0")
            .add("sec-ch-ua-platform", "\"Windows\"")
            .add("Sec-Fetch-Dest", "document")
            .add("Sec-Fetch-Mode", "navigate")
            .add("Sec-Fetch-Site", "none")
            .add("Sec-Fetch-User", "?1")
            .add("Upgrade-Insecure-Requests", "1")
            .build()

        fun isPublic(a: InetAddress): Boolean {
            if (a.isLoopbackAddress || a.isLinkLocalAddress || a.isSiteLocalAddress || a.isAnyLocalAddress || a.isMulticastAddress) {
                return false
            }
            val b = a.address
            return when (a) {
                is Inet4Address -> {
                    val b0 = b[0].toInt() and 0xFF
                    val b1 = b[1].toInt() and 0xFF
                    !(b0 == 100 && b1 in 64..127) && b0 != 0 && !(b0 == 169 && b1 == 254) && b0 < 224   // CGNAT, 0/8, link-local, multicast+
                }
                is Inet6Address -> (b[0].toInt() and 0xFE) != 0xFC                                          // not fc00::/7 (ULA)
                else -> false
            }
        }
    }
}
