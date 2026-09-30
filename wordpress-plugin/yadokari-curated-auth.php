<?php
/**
 * Plugin Name: YADOKARI CURATED auth
 * Description: キュレーション記事システム（yadokari-curated）の REST API ログインを通す。サーバーやWAFが Authorization ヘッダーを捨てる環境向けに、同じアプリケーションパスワードを X-YC-Auth ヘッダーでも受け付ける。
 * Version: 1.0
 *
 * 置き場所: wp-content/mu-plugins/yadokari-curated-auth.php（mu-plugins は有効化の操作が要らない）
 *
 * 2026-09-30: yadokari.net では Authorization ヘッダーが PHP に届かず（.htaccess を足しても同じ）、
 * REST API に 401「現在ログインしていません」が返った。ヘッダー名を変えて渡す。
 *
 * - 受け付けるのは REST API へのリクエストで、HTTPS のときだけ
 * - 確かめ方は WordPress 標準のアプリケーションパスワードと同じ（wp_authenticate_application_password）。
 *   通常のログインパスワードでは通らない
 * - 失敗しても何もしない（匿名のまま。WordPress の通常の判定に任せる）
 */

if (!defined('ABSPATH')) {
    exit;
}

add_filter('determine_current_user', function ($user_id) {
    if ($user_id) {
        return $user_id;
    }
    $header = isset($_SERVER['HTTP_X_YC_AUTH']) ? trim((string) $_SERVER['HTTP_X_YC_AUTH']) : '';
    if ($header === '' || !is_ssl()) {
        return $user_id;
    }
    $uri = isset($_SERVER['REQUEST_URI']) ? (string) $_SERVER['REQUEST_URI'] : '';
    if (strpos($uri, '/wp-json/') === false && strpos($uri, 'rest_route=') === false) {
        return $user_id;
    }
    if (stripos($header, 'Basic ') === 0) {
        $header = substr($header, 6);
    }
    $decoded = base64_decode($header, true);
    if ($decoded === false || strpos($decoded, ':') === false) {
        return $user_id;
    }
    list($username, $password) = explode(':', $decoded, 2);

    // この時点では REST_REQUEST が未定義のことがあるので、この呼び出しの間だけ API リクエストとして扱う
    $as_api = function () {
        return true;
    };
    add_filter('application_password_is_api_request', $as_api);
    $user = wp_authenticate_application_password(null, $username, $password);
    remove_filter('application_password_is_api_request', $as_api);

    if ($user instanceof WP_User) {
        return $user->ID;
    }
    return $user_id;
}, 30);
