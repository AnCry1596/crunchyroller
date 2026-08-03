package com.crunchyroller.auth

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.*
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.crunchyroller.R

/**
 * Dedicated Activity containing a WebView for user authentication.
 * Manages CookieManager session persistence, JavaScript / DOM storage settings,
 * auth completion detection, and network error handling.
 */
class WebViewActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var errorLayout: View
    private lateinit var errorTextView: TextView
    private lateinit var retryButton: Button

    private lateinit var sessionManager: SessionManager
    private var initialUrl: String = DEFAULT_LOGIN_URL

    companion object {
        const val EXTRA_LOGIN_URL = "extra_login_url"
        private const val DEFAULT_LOGIN_URL = "https://www.crunchyroll.com/login"
        private const val AUTH_SUCCESS_INDICATOR_COOKIE = "etp_rt"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_web_view)

        sessionManager = SessionManager.getInstance(this)
        initialUrl = intent.getStringExtra(EXTRA_LOGIN_URL) ?: DEFAULT_LOGIN_URL

        bindViews()
        setupWebView()
        loadLoginUrl()
    }

    private fun bindViews() {
        webView = findViewById(R.id.webView)
        progressBar = findViewById(R.id.progressBar)
        errorLayout = findViewById(R.id.errorLayout)
        errorTextView = findViewById(R.id.errorTextView)
        retryButton = findViewById(R.id.retryButton)

        retryButton.setOnClickListener {
            errorLayout.visibility = View.GONE
            webView.visibility = View.VISIBLE
            loadLoginUrl()
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true

        // Enable third-party cookies for standard authentication redirects
        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            cookieManager.setAcceptThirdPartyCookies(webView, true)
        }

        webView.webViewClient = object : WebViewClient() {

            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                super.onPageStarted(view, url, favicon)
                progressBar.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url?)
                progressBar.visibility = View.GONE

                url?.let { currentUrl ->
                    checkAuthCompletion(currentUrl)
                }
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                checkAuthCompletion(url)
                return false
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true) {
                    showError("Network connection failed. Please check your internet connection.")
                }
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                super.onReceivedHttpError(view, request, errorResponse)
                if (request?.isForMainFrame == true && errorResponse?.statusCode == 401) {
                    // Session expired or unauthorized
                    sessionManager.logout()
                }
            }
        }
    }

    private fun loadLoginUrl() {
        webView.loadUrl(initialUrl)
    }

    /**
     * Inspects CookieManager and navigation indicators to detect login completion.
     * Uses Android's standard CookieManager API exclusively.
     */
    private fun checkAuthCompletion(url: String) {
        val cookieManager = CookieManager.getInstance()
        val cookies = cookieManager.getCookie(url) ?: ""

        val hasSessionCookie = cookies.contains(AUTH_SUCCESS_INDICATOR_COOKIE)
        val isPostLoginNavigation = url.contains("crunchyroll.com/discover") ||
                url.contains("crunchyroll.com/home") ||
                (url.contains("crunchyroll.com") && !url.contains("/login") && !url.contains("/welcome"))

        if (hasSessionCookie || isPostLoginNavigation) {
            // Save session & flush cookies
            val host = Uri.parse(url).host ?: "crunchyroll.com"
            sessionManager.saveSession("https://$host")

            setResult(RESULT_OK)
            finish()
        }
    }

    private fun showError(message: String) {
        progressBar.visibility = View.GONE
        webView.visibility = View.GONE
        errorLayout.visibility = View.VISIBLE
        errorTextView.text = message
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }
}
