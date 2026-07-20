package com.vidzap.share

import android.app.AlertDialog
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject
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
                analyzeUrl(serverUrl, url)
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

    private fun showLoadingDialog(message: String): AlertDialog {
        val progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleLarge)
        progressBar.isIndeterminate = true

        val textView = TextView(this)
        textView.text = message
        textView.gravity = Gravity.CENTER
        textView.textSize = 16f
        textView.setPadding(0, 24, 0, 0)

        val layout = LinearLayout(this)
        layout.orientation = LinearLayout.VERTICAL
        layout.setPadding(48, 32, 48, 32)
        layout.gravity = Gravity.CENTER
        layout.addView(
            progressBar,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { gravity = Gravity.CENTER },
        )
        layout.addView(textView)

        return AlertDialog.Builder(this)
            .setView(layout)
            .setCancelable(false)
            .show()
    }

    private fun analyzeUrl(serverUrl: String, url: String) {
        val loadingDialog = showLoadingDialog("正在分析链接…")
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
                if (responseCode != 200) {
                    val errorBody = conn.errorStream?.bufferedReader()?.readText() ?: ""
                    val msg = "请求失败 ($responseCode): ${errorBody.take(200)}"
                    runOnUiThread {
                        loadingDialog.dismiss()
                        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                        finish()
                    }
                    conn.disconnect()
                    return@Thread
                }

                val body = conn.inputStream.bufferedReader().readText()
                conn.disconnect()

                val result = JSONObject(body)
                val status = result.optString("status", "")

                if (status == "analyzed") {
                    val title = result.optString("title", url)
                    val formats = result.optJSONArray("formats")
                    if (formats != null && formats.length() > 0) {
                        runOnUiThread {
                            loadingDialog.dismiss()
                            showFormatPicker(serverUrl, url, title, formats)
                        }
                    } else {
                        runOnUiThread {
                            loadingDialog.dismiss()
                            Toast.makeText(this, "未找到可用格式", Toast.LENGTH_LONG).show()
                            finish()
                        }
                    }
                } else if (status == "ok") {
                    val type = result.optString("type", "video")
                    val title = result.optString("title", url)
                    val label = if (type == "douyin_note") "笔记" else "视频"
                    runOnUiThread {
                        loadingDialog.dismiss()
                        Toast.makeText(this, "已添加 $label「${title.take(30)}」到下载队列", Toast.LENGTH_LONG).show()
                        finish()
                    }
                } else {
                    val msg = result.optString("message", "未知响应")
                    runOnUiThread {
                        loadingDialog.dismiss()
                        Toast.makeText(this, "处理失败: $msg", Toast.LENGTH_LONG).show()
                        finish()
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    loadingDialog.dismiss()
                    Toast.makeText(this, "连接失败: ${e.message}", Toast.LENGTH_LONG).show()
                    finish()
                }
            }
        }.start()
    }

    private fun showFormatPicker(serverUrl: String, url: String, title: String, formats: JSONArray) {
        val labels = Array(formats.length()) { i ->
            val f = formats.getJSONObject(i)
            val label = f.optString("label", "")
            val vcodec = f.optString("vcodec", "").split(".").firstOrNull() ?: ""
            val acodec = f.optString("acodec", "").split(".").firstOrNull() ?: ""
            val size = f.optLong("filesize", 0)
            val codecInfo = buildString {
                if (vcodec != "none") append(vcodec)
                if (acodec != "none") {
                    if (isNotEmpty()) append(" + ")
                    append(acodec)
                }
            }
            val sizeText = if (size > 0) " · ${size}MB" else ""
            "$label ($codecInfo)$sizeText"
        }

        AlertDialog.Builder(this)
            .setTitle("选择画质 - ${title.take(30)}")
            .setSingleChoiceItems(labels, -1) { dialog, which ->
                val formatId = formats.getJSONObject(which).optString("format_id", "")
                dialog.dismiss()
                downloadWithFormat(serverUrl, url, formatId)
            }
            .setNegativeButton("取消") { dialog, _ ->
                dialog.dismiss()
                finish()
            }
            .setOnCancelListener { finish() }
            .show()
    }

    private fun downloadWithFormat(serverUrl: String, url: String, formatId: String) {
        val loadingDialog = showLoadingDialog("正在提交下载…")
        Thread {
            try {
                val apiUrl = URL("$serverUrl/api/share")
                val conn = apiUrl.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 15000
                conn.readTimeout = 15000

                val json = """{"url":"$url","format_id":"$formatId"}"""
                OutputStreamWriter(conn.outputStream).use { it.write(json) }

                val responseCode = conn.responseCode
                val msg = if (responseCode == 200) {
                    "已添加到 VidZap 下载队列"
                } else {
                    val errorBody = conn.errorStream?.bufferedReader()?.readText() ?: ""
                    "请求失败 ($responseCode): ${errorBody.take(200)}"
                }
                runOnUiThread {
                    loadingDialog.dismiss()
                    Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                }
                conn.disconnect()
            } catch (e: Exception) {
                runOnUiThread {
                    loadingDialog.dismiss()
                    Toast.makeText(this, "连接失败: ${e.message}", Toast.LENGTH_LONG).show()
                }
            } finally {
                finish()
            }
        }.start()
    }
}
