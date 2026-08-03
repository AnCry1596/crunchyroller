package com.crunchyroller.auth

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.webkit.CookieManager
import android.webkit.ValueCallback

/**
 * Manages authenticated session persistence and CookieManager operations.
 * Encapsulates all cookie management within standard Android APIs.
 */
class SessionManager private constructor(context: Context) {

    private val prefs: SharedPreferences = context.applicationContext.getSharedPreferences(
        PREFS_NAME,
        Context.MODE_PRIVATE
    )

    companion object {
        private const val PREFS_NAME = "crunchyroller_session_prefs"
        private const val KEY_IS_LOGGED_IN = "is_logged_in"
        private const val KEY_LAST_LOGIN_TIME = "last_login_time"
        private const val KEY_AUTH_DOMAIN = "auth_domain"

        @Volatile
        private var INSTANCE: SessionManager? = null

        fun getInstance(context: Context): SessionManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: SessionManager(context).also { INSTANCE = it }
            }
        }
    }

    /**
     * Checks if the user is currently authenticated.
     */
    fun isAuthenticated(): Boolean {
        val isLoggedInPref = prefs.getBoolean(KEY_IS_LOGGED_IN, false)
        if (!isLoggedInPref) return false

        // Verify that CookieManager actually holds cookies for the domain
        val domain = getAuthDomain() ?: return false
        val cookieString = CookieManager.getInstance().getCookie(domain)
        return !cookieString.isNull_or_empty_session()
    }

    /**
     * Records a successful login session.
     */
    fun saveSession(authDomain: String) {
        prefs.edit().apply {
            putBoolean(KEY_IS_LOGGED_IN, true)
            putLong(KEY_LAST_LOGIN_TIME, System.currentTimeMillis())
            putString(KEY_AUTH_DOMAIN, authDomain)
            apply()
        }
        // Force cookie sync to disk
        CookieManager.getInstance().flush()
    }

    /**
     * Gets the authenticated domain.
     */
    fun getAuthDomain(): String? {
        return prefs.getString(KEY_AUTH_DOMAIN, "https://www.crunchyroll.com")
    }

    /**
     * Clears all session cookies and resets login state using standard Android CookieManager.
     */
    fun logout(onComplete: (() -> Unit)? = null) {
        // Clear local preferences
        prefs.edit().clear().apply()

        // Clear WebView cookies via CookieManager API
        val cookieManager = CookieManager.getInstance()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            cookieManager.removeAllCookies(ValueCallback {
                cookieManager.flush()
                onComplete?.invoke()
            })
        } else {
            @Suppress("DEPRECATION")
            cookieManager.removeAllCookie()
            @Suppress("DEPRECATION")
            cookieManager.removeSessionCookie()
            onComplete?.invoke()
        }
    }

    private fun String?.isNull_or_empty_session(): Boolean {
        return this.isNullOrBlank() || !this.contains("etp_rt") && !this.contains("session")
    }
}
