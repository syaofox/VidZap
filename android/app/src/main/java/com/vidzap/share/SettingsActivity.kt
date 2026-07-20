package com.vidzap.share

import android.content.SharedPreferences
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL

class SettingsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        prefs = getSharedPreferences("config", MODE_PRIVATE)

        val serverUrlInput = findViewById<EditText>(R.id.server_url)
        val saveButton = findViewById<Button>(R.id.save_button)
        val testButton = findViewById<Button>(R.id.test_button)

        val defaultUrl = "http://10.10.10.2:9112/"
        serverUrlInput.setText(prefs.getString("server_url", defaultUrl))

        saveButton.setOnClickListener {
            val url = serverUrlInput.text.toString().trim()
            if (url.isBlank()) {
                Toast.makeText(this, "请输入服务器地址", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit().putString("server_url", url).apply()
            Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show()
        }

        testButton.setOnClickListener {
            val url = serverUrlInput.text.toString().trim()
            if (url.isBlank()) {
                Toast.makeText(this, "请输入服务器地址", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            testButton.isEnabled = false
            testButton.text = "测试中..."
            Thread { testConnection(url, testButton) }.start()
        }
    }

    private fun testConnection(serverUrl: String, button: Button) {
        try {
            val url = URL(serverUrl.trimEnd('/'))
            val conn = url.openConnection() as HttpURLConnection
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            conn.instanceFollowRedirects = true
            conn.connect()
            val code = conn.responseCode
            conn.disconnect()
            runOnUiThread {
                button.isEnabled = true
                button.text = "测试连接"
                if (code in 200..499) {
                    Toast.makeText(this, "连接成功 (HTTP $code)", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this, "服务器返回异常状态: $code", Toast.LENGTH_SHORT).show()
                }
            }
        } catch (e: Exception) {
            runOnUiThread {
                button.isEnabled = true
                button.text = "测试连接"
                Toast.makeText(this, "连接失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }
}
