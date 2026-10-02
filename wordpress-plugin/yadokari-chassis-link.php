<?php
/**
 * Plugin Name: YADOKARI chassis link
 * Description: 「シャーシを購入する」のリンク先（/chassis/）を、商品検索（シャーシ）の結果ページに差し替える。
 * Version: 1.0
 *
 * 置き場所: wp-content/mu-plugins/yadokari-chassis-link.php（有効化の操作は要らない）
 * 外すとき: このファイルを消すだけで元に戻る（テーマのファイルは触っていない）
 *
 * 2026-10-02 ユーザー指定。リンクはテーマ（yadokari2025）に直接書かれているので、
 * 表のページを出す直前に href="https://yadokari.net/chassis/" だけを置き換える。
 * 対象: 上部メニュー・もう1つのメニュー・トップページの CHASSIS カード（同じ URL の3か所）。
 * /chassis/ のページ自体や、ほかの URL（/chassis/xxx/ など）は変えない。
 */

if (!defined('ABSPATH')) {
    exit;
}

const YC_CHASSIS_FROM = 'https://yadokari.net/chassis/';
const YC_CHASSIS_TO = 'https://yadokari.net/?search_element_1%5B%5D=2219&searchbutton=search&csp=search_add&feadvns_max_line_0=4&fe_form_no=0';

add_action('template_redirect', function () {
    if (is_admin() || wp_doing_ajax() || (defined('REST_REQUEST') && REST_REQUEST) || is_feed()) {
        return;
    }
    ob_start(function ($html) {
        $to = esc_url(YC_CHASSIS_TO);
        return str_replace(
            array('href="' . YC_CHASSIS_FROM . '"', "href='" . YC_CHASSIS_FROM . "'"),
            array('href="' . $to . '"', "href='" . $to . "'"),
            $html
        );
    });
}, 0);
