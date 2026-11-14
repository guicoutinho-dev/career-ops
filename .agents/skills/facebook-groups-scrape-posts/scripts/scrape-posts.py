#!/usr/bin/env python3
"""Generates a self-contained browser JS snippet to scrape Facebook Group posts via Comet GraphQL."""

import argparse
import json


def build_js_payload(sort: str, count: int, max_pages: int, doc_id: str) -> str:
    config = json.dumps({
        "sort": sort,
        "count": count,
        "maxPages": max_pages,
        "docId": doc_id,
    })
    return f"""(async () => {{
  const CFG = {config};
  const html = document.documentElement.innerHTML;

  const groupIdMatch =
    html.match(/"groupID"\\s*:\\s*"(\\d+)"/) ||
    html.match(/"group_id"\\s*:\\s*"(\\d+)"/) ||
    html.match(/fb:\\/\\/group\\/(\\d+)/) ||
    location.pathname.match(/\\/groups\\/(\\d+)/);
  const group_id = groupIdMatch ? groupIdMatch[1] : null;

  const groupNameMatch =
    html.match(/"Group"[^}}]*"name"\\s*:\\s*"([^"]+)"/) ||
    [null, document.title.replace(/\\s*\\|\\s*Facebook.*$/i, '').trim() || null];
  let group_name = groupNameMatch ? groupNameMatch[1] : null;

  let fb_dtsg = null;
  let user_id = document.cookie.match(/c_user=(\\d+)/)?.[1] || '0';
  try {{ fb_dtsg = require('DTSGInitialData').token || require('DTSG').getToken(); }} catch (e) {{}}
  try {{ user_id = require('CurrentUserInitialData').USER_ID || user_id; }} catch (e) {{}}

  if (!group_id) {{
    return JSON.stringify({{ ok: false, error: 'Could not resolve numeric group_id from current page', url: location.href }});
  }}

  const posts = [];
  const seenPostIds = new Set();
  const pagesDiag = [];
  let cursor = null;
  let hasNext = true;

  function extractPost(node) {{
    if (!node || typeof node !== 'object') return null;
    const post_id = node.post_id || node.subscription_target_id || null;
    if (!post_id) return null;

    const cometSections = node.comet_sections || {{}};
    const contextLayout = cometSections.context_layout?.story?.comet_sections || {{}};
    const metadataArr = contextLayout.metadata || [];
    let creation_time = null;
    let permalink_url = node.wwwURL || null;
    for (const m of metadataArr) {{
      const st = m?.story;
      if (st?.creation_time && !creation_time) creation_time = st.creation_time;
      if (st?.url && !permalink_url) permalink_url = st.url;
    }}
    if (!creation_time) {{
      creation_time = cometSections.timestamp?.story?.creation_time || node.creation_time || null;
    }}
    if (!permalink_url) {{
      permalink_url = `https://www.facebook.com/groups/${{group_id}}/posts/${{post_id}}/`;
    }}

    const msgStory = cometSections.content?.story || node;
    const message = msgStory?.message?.text || node?.message?.text || null;

    const actor = (msgStory?.actors && msgStory.actors[0]) || (node?.actors && node.actors[0]) || {{}};
    const author = {{
      id: actor.id || null,
      name: actor.name || null,
      profile_picture: actor.profile_picture?.uri || null,
      url: actor.url || null
    }};

    const toGroup = node.to || {{}};
    const grp = {{
      id: toGroup.id || group_id,
      name: toGroup.name || group_name,
      url: toGroup.url || `https://www.facebook.com/groups/${{group_id}}/`
    }};
    if (toGroup.name && !group_name) group_name = toGroup.name;

    const feedback = cometSections.feedback?.story?.feedback_context?.feedback_target_with_context?.ufi_renderer?.feedback ||
                     node.feedback || {{}};
    const reactCount = feedback.reaction_count?.count ?? 0;
    const reactFormatted = feedback.cannot_see_top_custom_reactions?.top_reactions?.summary ||
                           String(reactCount);
    const topEdges = feedback.top_reactions?.edges || [];
    const breakdown = topEdges.map(e => ({{
      name: e?.node?.localized_name || null,
      reaction_id: e?.node?.id || null,
      count: e?.reaction_count ?? 0
    }}));

    const share_count = feedback.share_count?.count ?? 0;
    const share_count_formatted = String(share_count);
    const comment_count = feedback.total_comment_count ?? feedback.comment_rendering_instance?.comments?.total_count ?? 0;

    const media = [];
    const attachments = msgStory?.attachments || node?.attachments || [];
    for (const att of attachments) {{
      const target = att?.styles?.attachment || att;
      const subNodes = target?.all_subattachments?.nodes || (target?.media ? [target] : []);
      for (const item of subNodes) {{
        const m = item?.media || item;
        if (!m || !m.__typename) continue;
        if (m.__typename === 'Photo') {{
          media.push({{
            __typename: 'Photo',
            id: m.id || null,
            photo_image: m.photo_image?.uri || m.image?.uri || m.viewer_image?.uri || null
          }});
        }} else if (m.__typename === 'Video') {{
          media.push({{
            __typename: 'Video',
            id: m.id || null,
            playable_url: m.playable_url || m.browser_native_sd_url || null,
            playable_url_hd: m.playable_url_quality_hd || m.browser_native_hd_url || null,
            thumbnail: m.preferred_thumbnail?.image?.uri || null
          }});
        }}
      }}
    }}

    return {{
      post_id: String(post_id),
      cache_id: node.cache_id || null,
      id: node.id || null,
      permalink_url,
      creation_time,
      message,
      author,
      group: grp,
      reactions: {{
        total: reactCount,
        total_formatted: String(reactFormatted),
        breakdown
      }},
      share_count,
      share_count_formatted,
      comment_count,
      media
    }};
  }}

  for (let pageIdx = 0; pageIdx < CFG.maxPages && posts.length < CFG.count && hasNext; pageIdx++) {{
    const variables = {{
      UFI2CommentsProvider_commentsKey: 'CometGroupDiscussionRootSuccessQuery',
      count: 3,
      cursor: cursor,
      displayCommentsContextEnableComment: null,
      displayCommentsContextIsAdPreview: null,
      displayCommentsContextIsAggregatedShare: null,
      displayCommentsContextIsStorySet: null,
      displayCommentsFeedbackContext: null,
      feedLocation: 'GROUP',
      feedType: 'DISCUSSION',
      feedbackSource: 0,
      filterTopicId: null,
      focusCommentID: null,
      id: group_id,
      youthIntegrityHostID: group_id,
      privacySelectorRenderLocation: 'COMET_STREAM',
      referringStoryRenderLocation: null,
      renderLocation: 'group',
      scale: 1,
      sortingSetting: CFG.sort,
      stream_initial_count: 1,
      useDefaultActor: false,
      __relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider: true,
      __relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider: true,
      __relay_internal__pv__CometFeedStory_enable_reactor_facepilerelayprovider: false,
      __relay_internal__pv__CometFeedStory_enable_social_bubblesrelayprovider: false,
      __relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider: false,
      __relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider: true,
      __relay_internal__pv__IsWorkUserrelayprovider: false,
      __relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider: false,
      __relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider: false,
      __relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider: true,
      __relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider: true,
      __relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider: true,
      __relay_internal__pv__CometFeedShareMedia_shouldPrefetchShareImagerelayprovider: true,
      __relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider: false,
      __relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider: false,
      __relay_internal__pv__IsMergQAPollsrelayprovider: false,
      __relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider: true,
      __relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider: false,
      __relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider: 'AUTO_TRANSLATE',
      __relay_internal__pv__GeminiUFISaveToKnowledgeHubGKrelayprovider: false,
      __relay_internal__pv__CometFeedStoryDynamicResolutionPhotoAttachmentRenderer_showWebpInCometFeedrelayprovider: false,
      __relay_internal__pv__CometUFIShareActionMigrationrelayprovider: true,
      __relay_internal__pv__CometUFISingleLineUFIrelayprovider: true,
      __relay_internal__pv__relay_provider_comet_ufi_ssr_seo_deferrelayprovider: true,
      __relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider: true,
      __relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider: 206,
      __relay_internal__pv__StoriesShouldEnablePhotosensitiveContentWarningrelayprovider: false,
      __relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider: false,
      __relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider: true
    }};

    const body = new URLSearchParams({{
      av: user_id,
      __user: user_id,
      __a: '1',
      fb_dtsg: fb_dtsg || '',
      fb_api_caller_class: 'RelayModern',
      fb_api_req_friendly_name: 'GroupsCometFeedRegularStoriesPaginationQuery',
      variables: JSON.stringify(variables),
      server_timestamps: 'true',
      doc_id: CFG.docId
    }});

    let httpStatus = 0;
    let edgeCount = 0;
    let err = null;

    try {{
      const resp = await fetch('/api/graphql/', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-FB-Friendly-Name': 'GroupsCometFeedRegularStoriesPaginationQuery'
        }},
        body: body.toString(),
        credentials: 'include'
      }});
      httpStatus = resp.status;
      const rawText = await resp.text();
      const lines = rawText.split(/\\r?\\n/).filter(Boolean);

      for (const line of lines) {{
        let parsed;
        try {{ parsed = JSON.parse(line); }} catch (e) {{ continue; }}
        if (parsed.errors && parsed.errors.length > 0 && !err) {{
          err = parsed.errors[0].message || 'GraphQL error';
        }}
        const feed = parsed?.data?.node?.group_feed;
        if (feed) {{
          const edges = feed.edges || [];
          edgeCount += edges.length;
          for (const edge of edges) {{
            const p = extractPost(edge?.node);
            if (p && p.post_id && p.permalink_url && p.creation_time && !seenPostIds.has(p.post_id)) {{
              seenPostIds.add(p.post_id);
              posts.push(p);
              if (posts.length >= CFG.count) break;
            }}
          }}
          if (feed.page_info) {{
            cursor = feed.page_info.end_cursor || cursor;
            hasNext = Boolean(feed.page_info.has_next_page && cursor);
          }}
        }}
        const streamNode = parsed?.data?.node;
        if (streamNode && (streamNode.__typename === 'Story' || streamNode.post_id)) {{
          edgeCount += 1;
          const p = extractPost(streamNode);
          if (p && p.post_id && p.permalink_url && p.creation_time && !seenPostIds.has(p.post_id) && posts.length < CFG.count) {{
            seenPostIds.add(p.post_id);
            posts.push(p);
          }}
          if (parsed?.data?.cursor) {{
            cursor = parsed.data.cursor;
          }}
        }}
        const deferPageInfo = parsed?.data?.page_info;
        if (deferPageInfo) {{
          cursor = deferPageInfo.end_cursor || cursor;
          hasNext = Boolean(deferPageInfo.has_next_page && cursor);
        }}
      }}
    }} catch (e) {{
      err = String(e.message || e);
      hasNext = false;
    }}

    pagesDiag.push({{ pageIdx, httpStatus, edgeCount, err, hasNext }});
    if (edgeCount === 0 && err) break;
  }}

  if (CFG.sort === 'CHRONOLOGICAL') {{
    posts.sort((a, b) => (b.creation_time || 0) - (a.creation_time || 0));
  }}

  return JSON.stringify({{
    ok: posts.length > 0,
    group_id,
    group_name,
    sort: CFG.sort,
    total: posts.length,
    posts: posts.slice(0, CFG.count),
    diagnostics: {{ pages: pagesDiag }}
  }});
}})()"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit JS for scraping Facebook Group posts")
    parser.add_argument(
        "--sort",
        choices=["TOP_POSTS", "CHRONOLOGICAL", "RECENT_ACTIVITY"],
        default="CHRONOLOGICAL",
        help="Sort order for group feed",
    )
    parser.add_argument("--count", type=int, default=20, help="Desired number of posts")
    parser.add_argument("--max-pages", type=int, default=100, help="Pagination safety cap")
    parser.add_argument(
        "--doc-id",
        default="29628468540078768",
        help="GraphQL persisted query doc_id for GroupsCometFeedRegularStoriesPaginationQuery",
    )
    args = parser.parse_args()
    print(build_js_payload(args.sort, args.count, args.max_pages, args.doc_id))


if __name__ == "__main__":
    main()
