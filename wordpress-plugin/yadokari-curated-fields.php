<?php
/**
 * Plugin Name: YADOKARI CURATED fields
 * Description: キュレーション記事システム（yadokari-curated）から、REST API に出ていない項目を受け取る。記事分類「TINY HOUSE JOURNAL」と ACF の QUOTE。
 * Version: 1.0
 *
 * 置き場所: wp-content/mu-plugins/yadokari-curated-fields.php（有効化の操作は要らない）
 *
 * 2026-09-30: 記事分類（post_category）と ACF の QUOTE は REST API に出ていないので、
 * 投稿の作成・更新のときに次の2つを受け取って書き込む。読み出し（GET）では何も返さない。
 * - yc_journal: true なら記事分類「TINY HOUSE JOURNAL」（tinyhousejournal）を付ける（ほかの分類は外さない）
 * - yc_quote:   ACF の QUOTE 欄に入れる文字列
 * 書き込めるのは、その投稿を編集できるユーザーだけ（REST API の通常の権限確認のあと）。
 */

if (!defined('ABSPATH')) {
    exit;
}

const YC_JOURNAL_TAXONOMY = 'post_category';
const YC_JOURNAL_TERM = 'tinyhousejournal';

/** ACF の「QUOTE」欄（ラベルか名前が quote）を投稿の種類から探す。見つからなければ null。 */
function yc_find_quote_field($post_id) {
    if (!function_exists('acf_get_field_groups') || !function_exists('acf_get_fields')) {
        return null;
    }
    foreach (acf_get_field_groups(array('post_id' => $post_id)) as $group) {
        foreach ((array) acf_get_fields($group) as $f) {
            if (strcasecmp((string) $f['name'], 'quote') === 0 || strcasecmp(trim((string) $f['label']), 'QUOTE') === 0) {
                return $f;
            }
        }
    }
    return null;
}

add_action('rest_api_init', function () {
    register_rest_field('post', 'yc_journal', array(
        'get_callback' => null,
        'update_callback' => function ($value, $post) {
            if (!$value) {
                return true;
            }
            if (!taxonomy_exists(YC_JOURNAL_TAXONOMY)) {
                return new WP_Error('yc_journal', '記事分類（post_category）がありません', array('status' => 500));
            }
            $r = wp_set_object_terms($post->ID, YC_JOURNAL_TERM, YC_JOURNAL_TAXONOMY, true);
            return is_wp_error($r) ? $r : true;
        },
        'schema' => array('type' => 'boolean', 'context' => array('edit')),
    ));

    register_rest_field('post', 'yc_quote', array(
        'get_callback' => null,
        'update_callback' => function ($value, $post) {
            $value = sanitize_textarea_field((string) $value);
            $field = yc_find_quote_field($post->ID);
            if ($field && function_exists('update_field')) {
                update_field($field['key'], $value, $post->ID);
            } else {
                update_post_meta($post->ID, 'quote', $value);
            }
            return true;
        },
        'schema' => array('type' => 'string', 'context' => array('edit')),
    ));
});
