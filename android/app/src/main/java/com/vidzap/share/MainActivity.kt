package com.vidzap.share

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("config", MODE_PRIVATE)

        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            val serverUrl = prefs.getString("server_url", "")?.trimEnd('/') ?: ""
            if (serverUrl.isEmpty()) {
                Toast.makeText(this, "请先配置服务器地址", Toast.LENGTH_SHORT).show()
                startActivity(Intent(this, SettingsActivity::class.java))
                finish()
                return
            }
            val sharedText = intent.getStringExtra(Intent.EXTRA_TEXT)?.trim() ?: ""
            val url = extractUrl(sharedText)
            if (url != null) {
                sendToVidZap(url)
            } else {
                Toast.makeText(this, "未在分享内容中找到链接", Toast.LENGTH_SHORT).show()
                finish()
            }
        } else {
            startActivity(Intent(this, SettingsActivity::class.java))
            finish()
        }
    }

    private fun extractUrl(text: String): String? {
        val urlPattern = Regex("https?://[\\w./?=&%-]+")
        return urlPattern.find(text)?.value
    }

    private fun sendToVidZap(url: String) {
        val serverUrl = prefs.getString("server_url", "")?.trimEnd('/') ?: ""

        Thread {
            try {
                val apiUrl = URL("$serverUrl/api/share")
                val conn = apiUrl.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 15000
                conn.readTimeout = 15000

                val json = """{"url":"$url"}"""
                OutputStreamWriter(conn.outputStream).use { it.write(json) }

                val responseCode = conn.responseCode
                val msg = if (responseCode == 200) {
                    "已添加到 VidZap 下载队列"
                } else {
                    val errorBody = conn.errorStream?.bufferedReader()?.readText() ?: ""
                    "请求失败 ($responseCode): ${errorBody.take(200)}"
                }
                runOnUiThread {
                    Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                }
                conn.disconnect()
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, "连接失败: ${e.message}", Toast.LENGTH_LONG).show()
                }
            } finally {
                finish()
            }
        }.start()
    }
}
