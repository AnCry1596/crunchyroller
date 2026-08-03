package com.crunchyroller.auth

import android.content.Context
import android.content.Intent

/**
 * Reusable entry point for handling user authentication flows in the app.
 * Abstracts login activity launching and status queries.
 */
class LoginManager private constructor(private val context: Context) {

    private val sessionManager = SessionManager.getInstance(context)

    interface AuthListener {
        fun onAuthSuccess()
        fun onAuthFailed(errorMsg: String)
    }

    companion object {
        @Volatile
        private var INSTANCE: LoginManager? = null

        fun getInstance(context: Context): LoginManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: LoginManager(context.applicationContext).also { INSTANCE = it }
            }
        }
    }

    /**
     * Determines whether the user is currently logged in.
     */
    fun isLoggedIn(): Boolean {
        return sessionManager.isAuthenticated()
    }

    /**
     * Opens the dedicated WebView login screen.
     */
    fun startLoginFlow(context: Context, targetUrl: String = "https://www.crunchyroll.com/login") {
        val intent = Intent(context, WebViewActivity::class.java).apply {
            putExtra(WebViewActivity.EXTRA_LOGIN_URL, targetUrl)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(intent)
    }

    /**
     * Performs logout and clears all stored WebView session cookies.
     */
    fun logout(onComplete: (() -> Unit)? = null) {
        sessionManager.logout(onComplete)
    }
}
