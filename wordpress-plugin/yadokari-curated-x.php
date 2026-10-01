<?php
/**
 * Plugin Name: YADOKARI CURATED X
 * Description: キュレーション記事（yadokari-curated）が公開された瞬間に、X（@yadokari_mobi）へ投稿する。
 * Version: 1.1
 *
 * 置き場所: wp-content/mu-plugins/yadokari-curated-x.php（有効化の操作は要らない）
 *
 * 鍵は wp-config.php にだけ置く（このファイルやリポジトリには書かない）:
 *   define('YC_X_API_KEY', '...');        // API Key（コンシューマーキー）
 *   define('YC_X_API_SECRET', '...');     // API Key Secret
 *   define('YC_X_ACCESS_TOKEN', '...');   // Access Token（読み書き）
 *   define('YC_X_ACCESS_SECRET', '...');  // Access Token Secret
 *
 * 動き（2026-09-30）:
 * - システムが REST で送った投稿文（yc_tweet_text）を post meta に持っておく
 * - その投稿が「公開」になった瞬間（予約の時刻・手で公開のどちらも）に、本文＋パーマリンクを X に投稿
 * - 1記事1回だけ（yc_tweet_id / yc_tweet_lock）。失敗したら yc_tweet_error に残す（再投稿はしない）
 * - 投稿文が無い記事（ほかの記事）には何もしない
 *
 * 1.1（2026-10-01）: 予約投稿が公開されても、W3 Total Cache のページキャッシュが残って
 * トップページの新着に出なかった。システムの記事が公開されたらページキャッシュを消す。
 */

if (!defined('ABSPATH')) {
    exit;
}

add_action('rest_api_init', function () {
    register_rest_field('post', 'yc_tweet_text', array(
        'get_callback' => null,
        'update_callback' => function ($value, $post) {
            $text = sanitize_textarea_field((string) $value);
            if ($text === '') {
                delete_post_meta($post->ID, 'yc_tweet_text');
            } else {
                update_post_meta($post->ID, 'yc_tweet_text', $text);
            }
            return true;
        },
        'schema' => array('type' => 'string', 'context' => array('edit')),
    ));
});

function yc_x_ready() {
    return defined('YC_X_API_KEY') && defined('YC_X_API_SECRET')
        && defined('YC_X_ACCESS_TOKEN') && defined('YC_X_ACCESS_SECRET');
}

/** X API v2 に OAuth 1.0a で投稿する。成功ならツイート ID、失敗なら WP_Error。 */
function yc_x_post($text) {
    $url = 'https://api.x.com/2/tweets';
    $oauth = array(
        'oauth_consumer_key' => YC_X_API_KEY,
        'oauth_nonce' => wp_generate_password(32, false),
        'oauth_signature_method' => 'HMAC-SHA1',
        'oauth_timestamp' => (string) time(),
        'oauth_token' => YC_X_ACCESS_TOKEN,
        'oauth_version' => '1.0',
    );
    ksort($oauth);
    $pairs = array();
    foreach ($oauth as $k => $v) {
        $pairs[] = rawurlencode($k) . '=' . rawurlencode($v);
    }
    // JSON の本文は署名に入れない（OAuth 1.0a の決まり）
    $base = 'POST&' . rawurlencode($url) . '&' . rawurlencode(implode('&', $pairs));
    $key = rawurlencode(YC_X_API_SECRET) . '&' . rawurlencode(YC_X_ACCESS_SECRET);
    $oauth['oauth_signature'] = base64_encode(hash_hmac('sha1', $base, $key, true));
    $header = array();
    foreach ($oauth as $k => $v) {
        $header[] = rawurlencode($k) . '="' . rawurlencode($v) . '"';
    }
    $res = wp_remote_post($url, array(
        'headers' => array(
            'Authorization' => 'OAuth ' . implode(', ', $header),
            'Content-Type' => 'application/json',
        ),
        'body' => wp_json_encode(array('text' => $text)),
        'timeout' => 20,
    ));
    if (is_wp_error($res)) {
        return $res;
    }
    $code = wp_remote_retrieve_response_code($res);
    $body = json_decode(wp_remote_retrieve_body($res), true);
    if ($code !== 201 || empty($body['data']['id'])) {
        return new WP_Error('yc_x', 'X API ' . $code . ': ' . substr(wp_remote_retrieve_body($res), 0, 300));
    }
    return (string) $body['data']['id'];
}

add_action('transition_post_status', function ($new, $old, $post) {
    if ($new !== 'publish' || $old === 'publish' || $post->post_type !== 'post') {
        return;
    }
    $text = get_post_meta($post->ID, 'yc_tweet_text', true);
    if (!$text || get_post_meta($post->ID, 'yc_tweet_id', true)) {
        return;
    }
    if (!yc_x_ready()) {
        update_post_meta($post->ID, 'yc_tweet_error', 'wp-config.php に YC_X_* の鍵がありません');
        return;
    }
    // 二重投稿の防止（同時に2回呼ばれても1回だけ）
    if (!add_post_meta($post->ID, 'yc_tweet_lock', time(), true)) {
        return;
    }
    $result = yc_x_post($text . "\n\n" . get_permalink($post->ID));
    if (is_wp_error($result)) {
        update_post_meta($post->ID, 'yc_tweet_error', $result->get_error_message());
        return;
    }
    update_post_meta($post->ID, 'yc_tweet_id', $result);
    delete_post_meta($post->ID, 'yc_tweet_error');
}, 10, 3);

// 1.1: システムの記事（slug が yc-）が公開されたら、W3 Total Cache のページキャッシュを消す
// （トップページ・一覧に新着が出るように）。X の投稿とは別に、必ず行う
add_action('transition_post_status', function ($new, $old, $post) {
    if ($new !== 'publish' || $old === 'publish' || $post->post_type !== 'post') {
        return;
    }
    if (strpos((string) $post->post_name, 'yc-') !== 0) {
        return;
    }
    if (function_exists('w3tc_flush_posts')) {
        w3tc_flush_posts();
    } elseif (function_exists('w3tc_pgcache_flush')) {
        w3tc_pgcache_flush();
    }
}, 20, 3);
