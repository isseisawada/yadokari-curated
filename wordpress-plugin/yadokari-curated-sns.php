<?php
/**
 * Plugin Name: YADOKARI CURATED SNS
 * Description: 公式 SNS（X・Threads @yadokari_mobi）へ、決めた時刻に投稿する。文面は yadokari-curated が作って REST で渡す。
 * Version: 1.0
 *
 * 置き場所: wp-content/mu-plugins/yadokari-curated-sns.php（有効化の操作は要らない）
 * 前提: yadokari-curated-x.php（X の投稿関数 yc_x_post と鍵 YC_X_*）が同じ mu-plugins にあること
 *
 * Threads の鍵は wp-config.php にだけ置く（このファイルやリポジトリには書かない）:
 *   define('YC_THREADS_TOKEN', '...');   // Threads の長期アクセストークン（@yadokari_mobi）
 * トークンは60日で切れるので、このプラグインが週1回自動で延長して option に保存する。
 *
 * 動き（2026-10-13 ユーザー指定）:
 * - POST /wp-json/yc/v1/sns  {key, kind, text, url, at, wp_post_id}  予約を入れる（key が同じなら上書き）
 *                            {key, cancel: true}                      取り消す
 * - GET  /wp-json/yc/v1/sns?keys=1,2,3                               結果（投稿 ID・エラー）
 * - GET  /wp-json/yc/v1/sns/metrics?threads=ID,ID                    Threads の数字とフォロワー数
 * - 時刻になったら X と Threads に「本文＋改行2つ＋URL」を投稿（URL の utm_source を x / threads に）
 * - kind=new（新着記事）は、記事がまだ公開されていなければ15分待つ（最大2時間）
 * - 1件1回だけ（yc_sns_x_id / yc_sns_th_id があれば投稿しない）
 * - これを置くと、yadokari-curated-x.php の「公開と同時の X 投稿（タイトル＋リンク）」は止まる
 *   （公式 SNS の投稿に置き換え。キャッシュ削除はそのまま動く）
 */

if (!defined('ABSPATH')) {
    exit;
}

const YC_SNS_TYPE = 'yc_sns';

add_action('init', function () {
    register_post_type(YC_SNS_TYPE, array(
        'label' => 'YC SNS',
        'public' => false,
        'show_ui' => false,
        'show_in_rest' => false,
        'supports' => array('title', 'custom-fields'),
    ));
});

// 公開と同時の X 投稿（yadokari-curated-x.php）を止める。あちらは yc_tweet_lock が付けられないと投稿しない
add_action('transition_post_status', function ($new, $old, $post) {
    if ($new === 'publish' && $old !== 'publish' && $post->post_type === 'post'
        && get_post_meta($post->ID, 'yc_tweet_text', true)) {
        add_post_meta($post->ID, 'yc_tweet_lock', 'sns', true);
    }
}, 5, 3);

function yc_sns_find($key) {
    $q = get_posts(array(
        'post_type' => YC_SNS_TYPE, 'post_status' => 'any', 'numberposts' => 1,
        'meta_key' => 'yc_sns_key', 'meta_value' => (string) intval($key), 'fields' => 'ids',
    ));
    return $q ? intval($q[0]) : 0;
}

function yc_sns_state($id) {
    $x = get_post_meta($id, 'yc_sns_x_id', true);
    $th = get_post_meta($id, 'yc_sns_th_id', true);
    $err = get_post_meta($id, 'yc_sns_error', true);
    $state = get_post_meta($id, 'yc_sns_state', true) ?: 'queued';
    return array(
        'state' => $state, 'x_id' => $x ?: null, 'threads_id' => $th ?: null,
        'posted_at' => get_post_meta($id, 'yc_sns_posted_at', true) ?: null, 'error' => $err ?: null,
    );
}

add_action('rest_api_init', function () {
    $can = function () {
        return current_user_can('edit_others_posts');
    };
    register_rest_route('yc/v1', '/sns', array(
        array('methods' => 'POST', 'permission_callback' => $can, 'callback' => function ($req) {
            $key = intval($req['key']);
            if (!$key) {
                return new WP_Error('yc_sns', 'key がありません', array('status' => 400));
            }
            $id = yc_sns_find($key);
            if ($req['cancel']) {
                if ($id) {
                    wp_clear_scheduled_hook('yc_sns_fire', array($id));
                    if (get_post_meta($id, 'yc_sns_state', true) !== 'posted') {
                        update_post_meta($id, 'yc_sns_state', 'cancelled');
                    }
                }
                return array('id' => $id, 'state' => 'cancelled');
            }
            $at = strtotime((string) $req['at']);
            $text = sanitize_textarea_field((string) $req['text']);
            $url = esc_url_raw((string) $req['url']);
            if (!$at || $text === '' || $url === '') {
                return new WP_Error('yc_sns', 'text / url / at が必要です', array('status' => 400));
            }
            if ($id && get_post_meta($id, 'yc_sns_state', true) === 'posted') {
                return new WP_Error('yc_sns', 'もう投稿済みです', array('status' => 409));
            }
            if (!$id) {
                $id = wp_insert_post(array('post_type' => YC_SNS_TYPE, 'post_status' => 'private',
                    'post_title' => 'sns ' . $key), true);
                if (is_wp_error($id)) {
                    return $id;
                }
                update_post_meta($id, 'yc_sns_key', (string) $key);
            }
            update_post_meta($id, 'yc_sns_text', $text);
            update_post_meta($id, 'yc_sns_url', $url);
            update_post_meta($id, 'yc_sns_at', $at);
            update_post_meta($id, 'yc_sns_kind', sanitize_key((string) $req['kind']));
            update_post_meta($id, 'yc_sns_wp_post', intval($req['wp_post_id']));
            update_post_meta($id, 'yc_sns_state', 'queued');
            delete_post_meta($id, 'yc_sns_error');
            delete_post_meta($id, 'yc_sns_wait');
            wp_clear_scheduled_hook('yc_sns_fire', array($id));
            wp_schedule_single_event($at, 'yc_sns_fire', array($id));
            return array('id' => $id, 'state' => 'queued', 'at' => gmdate('c', $at));
        }),
        array('methods' => 'GET', 'permission_callback' => $can, 'callback' => function ($req) {
            $out = array();
            foreach (array_filter(array_map('intval', explode(',', (string) $req['keys']))) as $key) {
                $id = yc_sns_find($key);
                $out[$key] = $id ? yc_sns_state($id) : array('state' => 'missing');
            }
            return $out;
        }),
    ));
    register_rest_route('yc/v1', '/sns/metrics', array(
        'methods' => 'GET', 'permission_callback' => $can, 'callback' => function ($req) {
            $posts = array();
            foreach (array_filter(explode(',', (string) $req['threads'])) as $mid) {
                $mid = preg_replace('/\D/', '', $mid);
                $r = yc_threads_get('/' . $mid . '/insights', array('metric' => 'views,likes,replies,reposts,quotes'));
                if (is_wp_error($r)) {
                    continue;
                }
                $m = array();
                foreach ($r['data'] ?? array() as $row) {
                    $m[$row['name']] = intval($row['values'][0]['value'] ?? ($row['total_value']['value'] ?? 0));
                }
                $posts[$mid] = $m;
            }
            $f = yc_threads_get('/me/threads_insights', array('metric' => 'followers_count'));
            $followers = is_wp_error($f) ? null : intval($f['data'][0]['total_value']['value'] ?? 0);
            return array('posts' => $posts, 'followers' => $followers);
        },
    ));
});

// ----------------------------------------------------------------------
// Threads
// ----------------------------------------------------------------------
function yc_threads_token() {
    $saved = get_option('yc_threads_token');
    if ($saved) {
        return $saved;
    }
    return defined('YC_THREADS_TOKEN') ? YC_THREADS_TOKEN : '';
}

function yc_threads_get($path, $args = array()) {
    $token = yc_threads_token();
    if (!$token) {
        return new WP_Error('yc_threads', 'wp-config.php に YC_THREADS_TOKEN がありません');
    }
    $url = add_query_arg(array_merge($args, array('access_token' => $token)), 'https://graph.threads.net/v1.0' . $path);
    $res = wp_remote_get($url, array('timeout' => 20));
    if (is_wp_error($res)) {
        return $res;
    }
    $body = json_decode(wp_remote_retrieve_body($res), true);
    if (wp_remote_retrieve_response_code($res) !== 200) {
        return new WP_Error('yc_threads', 'Threads API ' . wp_remote_retrieve_response_code($res) . ': ' . substr(wp_remote_retrieve_body($res), 0, 300));
    }
    return $body;
}

function yc_threads_post($text) {
    $token = yc_threads_token();
    if (!$token) {
        return new WP_Error('yc_threads', 'wp-config.php に YC_THREADS_TOKEN がありません');
    }
    $base = 'https://graph.threads.net/v1.0/me';
    $res = wp_remote_post($base . '/threads', array('timeout' => 20, 'body' => array(
        'media_type' => 'TEXT', 'text' => $text, 'access_token' => $token)));
    $body = is_wp_error($res) ? null : json_decode(wp_remote_retrieve_body($res), true);
    if (empty($body['id'])) {
        return new WP_Error('yc_threads', 'Threads（作成）: ' . (is_wp_error($res) ? $res->get_error_message() : substr(wp_remote_retrieve_body($res), 0, 300)));
    }
    $res = wp_remote_post($base . '/threads_publish', array('timeout' => 20, 'body' => array(
        'creation_id' => $body['id'], 'access_token' => $token)));
    $pub = is_wp_error($res) ? null : json_decode(wp_remote_retrieve_body($res), true);
    if (empty($pub['id'])) {
        return new WP_Error('yc_threads', 'Threads（公開）: ' . (is_wp_error($res) ? $res->get_error_message() : substr(wp_remote_retrieve_body($res), 0, 300)));
    }
    return (string) $pub['id'];
}

// 長期トークンは60日で切れる。週1回延長する
add_action('init', function () {
    if (!wp_next_scheduled('yc_threads_refresh')) {
        wp_schedule_event(time() + HOUR_IN_SECONDS, 'weekly', 'yc_threads_refresh');
    }
});
add_action('yc_threads_refresh', function () {
    $token = yc_threads_token();
    if (!$token) {
        return;
    }
    $res = wp_remote_get(add_query_arg(array('grant_type' => 'th_refresh_token', 'access_token' => $token),
        'https://graph.threads.net/refresh_access_token'), array('timeout' => 20));
    $body = is_wp_error($res) ? null : json_decode(wp_remote_retrieve_body($res), true);
    if (!empty($body['access_token'])) {
        update_option('yc_threads_token', $body['access_token'], false);
    }
});

// ----------------------------------------------------------------------
// 時刻になったら投稿する
// ----------------------------------------------------------------------
function yc_sns_url_for($url, $source) {
    return add_query_arg('utm_source', $source, $url);
}

add_action('yc_sns_fire', function ($id) {
    if (get_post_meta($id, 'yc_sns_state', true) !== 'queued') {
        return;
    }
    // 新着記事は、記事が公開されてから出す（予約公開が遅れたとき）
    $wp_post = intval(get_post_meta($id, 'yc_sns_wp_post', true));
    if (get_post_meta($id, 'yc_sns_kind', true) === 'new' && $wp_post && get_post_status($wp_post) !== 'publish') {
        $wait = intval(get_post_meta($id, 'yc_sns_wait', true)) + 1;
        update_post_meta($id, 'yc_sns_wait', $wait);
        if ($wait <= 8) {
            wp_schedule_single_event(time() + 15 * MINUTE_IN_SECONDS, 'yc_sns_fire', array($id));
        } else {
            update_post_meta($id, 'yc_sns_state', 'failed');
            update_post_meta($id, 'yc_sns_error', '記事が公開されないので出しませんでした');
        }
        return;
    }
    // 二重投稿の防止（同時に2回呼ばれても1回だけ）
    if (!add_post_meta($id, 'yc_sns_lock', time(), true)) {
        return;
    }
    $text = get_post_meta($id, 'yc_sns_text', true);
    $url = get_post_meta($id, 'yc_sns_url', true);
    $errors = array();
    if (!get_post_meta($id, 'yc_sns_x_id', true)) {
        if (!function_exists('yc_x_post') || !function_exists('yc_x_ready') || !yc_x_ready()) {
            $errors[] = 'X: 鍵か yadokari-curated-x.php がありません';
        } else {
            $r = yc_x_post($text . "\n\n" . yc_sns_url_for($url, 'x'));
            if (is_wp_error($r)) {
                $errors[] = 'X: ' . $r->get_error_message();
            } else {
                update_post_meta($id, 'yc_sns_x_id', $r);
            }
        }
    }
    if (!get_post_meta($id, 'yc_sns_th_id', true)) {
        $r = yc_threads_post($text . "\n\n" . yc_sns_url_for($url, 'threads'));
        if (is_wp_error($r)) {
            $errors[] = $r->get_error_message();
        } else {
            update_post_meta($id, 'yc_sns_th_id', $r);
        }
    }
    $any = get_post_meta($id, 'yc_sns_x_id', true) || get_post_meta($id, 'yc_sns_th_id', true);
    update_post_meta($id, 'yc_sns_state', $any ? 'posted' : 'failed');
    update_post_meta($id, 'yc_sns_posted_at', gmdate('c'));
    if ($errors) {
        update_post_meta($id, 'yc_sns_error', implode(' / ', $errors));
    }
    delete_post_meta($id, 'yc_sns_lock');
});
