<?php
/**
 * Plugin Name: YADOKARI Curated SEO
 * Description: 記事ページに構造化データ（JSON-LD: Article・BreadcrumbList）を出す。yadokari-curated から送る物件の事実（post meta: yc_facts）も載せる。
 * Version: 0.1.0
 * Author: YADOKARI
 *
 * 2026-09-29 時点の yadokari.net は構造化データを1つも出していない（記事 96036 で確認）。
 * 検索エンジンと AI の回答（AI Overviews など）が、記事の種類・見出し・日付・画像・
 * 著者・パンくずを機械的に読めるようにする。
 *
 * 入れ方: wp-content/mu-plugins/ にこのファイルを置く（有効化の操作は要らない）。
 * **本番の前にステージングで確認する。** 他の SEO プラグインが構造化データを出すようになったら外す。
 */

if (!defined('ABSPATH')) {
    exit;
}

/** yadokari-curated が REST API で送る物件の事実（JSON 文字列）を受け取れるようにする。 */
add_action('init', function () {
    register_post_meta('post', 'yc_facts', [
        'type'          => 'string',
        'single'        => true,
        'show_in_rest'  => true,
        'auth_callback' => function () {
            return current_user_can('edit_posts');
        },
    ]);
});

/** 物件の種別 → 日本語。 */
function yc_seo_kind_label($kind)
{
    $map = [
        'tiny_house_on_wheels' => 'トレーラーハウス',
        'trailer_caravan'      => 'トレーラーハウス',
        'van_camper'           => 'キャンピングカー',
        'boat_floating'        => 'ボートハウス',
        'container'            => 'コンテナハウス',
        'treehouse'            => 'ツリーハウス',
        'cabin_hut'            => '小屋',
        'prefab_modular'       => 'プレハブ住宅',
        'small_house'          => 'スモールハウス',
    ];
    return isset($map[$kind]) ? $map[$kind] : 'タイニーハウス';
}

add_action('wp_head', function () {
    if (!is_singular('post')) {
        return;
    }
    $post = get_queried_object();
    if (!$post instanceof WP_Post) {
        return;
    }
    $url = get_permalink($post);
    $image = get_the_post_thumbnail_url($post, 'full');
    $tags = wp_get_post_tags($post->ID, ['fields' => 'names']);
    $cats = get_the_category($post->ID);
    $site = get_bloginfo('name');
    $icon = get_site_icon_url(512);

    $article = [
        '@context'         => 'https://schema.org',
        '@type'            => 'Article',
        'headline'         => wp_strip_all_tags(get_the_title($post)),
        'description'      => wp_strip_all_tags(get_the_excerpt($post)),
        'datePublished'    => get_the_date('c', $post),
        'dateModified'     => get_the_modified_date('c', $post),
        'mainEntityOfPage' => $url,
        'author'           => [
            '@type' => 'Person',
            'name'  => get_the_author_meta('display_name', $post->post_author),
        ],
        'publisher'        => array_filter([
            '@type' => 'Organization',
            'name'  => $site,
            'url'   => home_url('/'),
            'logo'  => $icon ? ['@type' => 'ImageObject', 'url' => $icon] : null,
        ]),
        'inLanguage'       => 'ja',
    ];
    if ($image) {
        $article['image'] = [$image];
    }
    if ($tags) {
        $article['keywords'] = implode(',', $tags);
    }
    if ($cats) {
        $article['articleSection'] = $cats[0]->name;
    }

    // yadokari-curated から送った物件の事実。記事が何について書かれているかを示す
    $facts = json_decode((string) get_post_meta($post->ID, 'yc_facts', true), true);
    if (is_array($facts) && !empty($facts['name'])) {
        $about = [
            '@type'       => 'Accommodation',
            'name'        => $facts['name'],
            'description' => yc_seo_kind_label(isset($facts['kind']) ? $facts['kind'] : ''),
        ];
        $place = array_filter([
            isset($facts['region']) ? $facts['region'] : null,
            isset($facts['country']) ? $facts['country'] : null,
        ]);
        if ($place) {
            $about['address'] = ['@type' => 'PostalAddress', 'addressLocality' => implode('、', $place)];
        }
        if (!empty($facts['area'])) {
            $about['floorSize'] = ['@type' => 'QuantitativeValue', 'description' => $facts['area']];
        }
        $makers = [];
        foreach (['builder', 'architect'] as $key) {
            if (!empty($facts[$key])) {
                $makers[] = ['@type' => 'Organization', 'name' => $facts[$key]];
            }
        }
        $article['about'] = $about;
        if ($makers) {
            $article['mentions'] = $makers;
        }
    }

    $crumbs = [['@type' => 'ListItem', 'position' => 1, 'name' => $site, 'item' => home_url('/')]];
    if ($cats) {
        $crumbs[] = ['@type' => 'ListItem', 'position' => 2, 'name' => $cats[0]->name,
                     'item' => get_category_link($cats[0]->term_id)];
    }
    $crumbs[] = ['@type' => 'ListItem', 'position' => count($crumbs) + 1,
                 'name' => wp_strip_all_tags(get_the_title($post)), 'item' => $url];
    $breadcrumb = ['@context' => 'https://schema.org', '@type' => 'BreadcrumbList', 'itemListElement' => $crumbs];

    foreach ([$article, $breadcrumb] as $data) {
        echo '<script type="application/ld+json">'
            . wp_json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_HEX_TAG)
            . "</script>\n";
    }
}, 20);
