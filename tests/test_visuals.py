"""Cover logos and generated slide images.

The one guarantee that matters most: nothing here can stop a deck from being
published. Every miss (no asset folder, an unreadable client site, a favicon-
sized logo, a failed or filtered generation) ends in a captioned placeholder.

All network calls (Drive, the client's site, Imagen) are mocked.
"""

from __future__ import annotations

import base64
import json
import struct
from unittest.mock import MagicMock, patch

import pytest

from agents.solutioning_agent.tools import deck as deck_tools
from agents.solutioning_agent.tools import master_deck, visuals


@pytest.fixture(autouse=True)
def _offline_visuals():
    """Overrides conftest's stub: these tests exercise the real functions."""
    visuals._generated.clear()
    visuals._ht_logo = None
    yield


def _png(w: int, h: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"


def _jpeg(w: int, h: int) -> bytes:
    # SOI, an APP0 segment to skip, then SOF0 carrying height and width.
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, h, w) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + sof0


def _site(pages: dict[str, bytes]):
    """Patch visuals._get to serve `pages` by url, failing on anything else."""
    def get(url, limit):
        if url not in pages:
            raise OSError(f"no route to {url}")
        return url, pages[url]
    return patch.object(visuals, "_get", side_effect=get)


# --- image sniffing -----------------------------------------------------------


def test_png_jpeg_and_gif_sizes_are_read_from_their_headers():
    assert visuals._image_type_and_size(_png(400, 120)) == ("image/png", 400, 120)
    assert visuals._image_type_and_size(_jpeg(640, 200)) == ("image/jpeg", 640, 200)
    gif = b"GIF89a" + struct.pack("<HH", 300, 90)
    assert visuals._image_type_and_size(gif) == ("image/gif", 300, 90)


@pytest.mark.parametrize("data", [
    b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    b"RIFF\x00\x00\x00\x00WEBPVP8 ",
    b"",
    b"\xff\xd8\xff",  # a truncated jpeg
])
def test_formats_slides_cannot_import_reliably_are_refused(data):
    assert visuals._image_type_and_size(data) is None


# --- the client's logo --------------------------------------------------------


SITE = "https://www.acme.in/"


def test_the_sites_structured_data_logo_is_preferred():
    page = b"""<html><head>
      <link rel="apple-touch-icon" href="/touch.png">
      <script type="application/ld+json">{"@type": "Organization", "logo": "https://cdn.acme.in/logo.png"}</script>
    </head><body><img class="site-logo" src="/header-logo.png"></body></html>"""
    with _site({SITE: page, "https://cdn.acme.in/logo.png": _png(600, 200),
                "https://www.acme.in/touch.png": _png(180, 180)}):
        uri = visuals.client_logo(SITE)
    assert uri == "data:image/png;base64," + base64.b64encode(_png(600, 200)).decode()


def test_a_logo_inside_a_json_ld_graph_is_found():
    page = b"""<script type="application/ld+json">
      {"@graph": [{"@type": "WebSite"}, {"@type": "Organization", "logo": {"url": "/brand.png"}}]}
    </script>"""
    with _site({SITE: page, "https://www.acme.in/brand.png": _png(500, 150)}):
        assert visuals.client_logo(SITE).startswith("data:image/png;base64,")


def test_an_image_marked_as_the_logo_is_used_with_its_relative_url_resolved():
    page = b'<header><img alt="Acme logo" src="img/acme.jpg"></header>'
    with _site({SITE: page, "https://www.acme.in/img/acme.jpg": _jpeg(480, 160)}):
        assert visuals.client_logo(SITE).startswith("data:image/jpeg;base64,")


def test_favicon_sized_and_svg_logos_are_skipped_for_a_usable_one():
    page = b"""<img class="logo" src="/logo.svg">
      <img id="logo-small" src="/tiny.png">
      <link rel="apple-touch-icon" sizes="180x180" href="/touch.png">"""
    with _site({SITE: page,
                "https://www.acme.in/logo.svg": b"<svg></svg>",
                "https://www.acme.in/tiny.png": _png(32, 32),
                "https://www.acme.in/touch.png": _png(180, 180)}):
        uri = visuals.client_logo(SITE)
    assert uri == "data:image/png;base64," + base64.b64encode(_png(180, 180)).decode()


def test_no_usable_logo_is_none_not_a_guess():
    page = b'<link rel="icon" href="/favicon.ico"><img src="/hero.jpg" alt="Our team">'
    with _site({SITE: page, "https://www.acme.in/hero.jpg": _jpeg(1600, 900)}):
        assert visuals.client_logo(SITE) is None


def test_other_companies_logos_on_the_page_are_never_taken():
    """AMD's homepage: partner logos under /images/logos/, the real one an SVG."""
    page = b"""<header><img class="logo" src="/amd-header-logo.svg"></header>
      <main><img src="/images/logos/products/agentic-ai-teaser.png">
      <img alt="AWS logo" src="/images/logos/partners/aws-white-padded-logo.png">
      <img src="/illustrations/homepage-logo-wall-background-enterprise-amd.jpg"></main>"""
    with _site({SITE: page,
                "https://www.acme.in/amd-header-logo.svg": b"<svg></svg>",
                "https://www.acme.in/images/logos/products/agentic-ai-teaser.png": _png(1200, 675),
                "https://www.acme.in/images/logos/partners/aws-white-padded-logo.png": _png(400, 200),
                "https://www.acme.in/illustrations/homepage-logo-wall-background-enterprise-amd.jpg": _jpeg(2560, 2471)}):
        assert visuals.client_logo(SITE, "AMD") is None


def test_a_logo_outside_the_header_counts_when_it_names_the_client():
    page = b'<footer><img src="/assets/acme-logo-dark.png"></footer>'
    with _site({SITE: page, "https://www.acme.in/assets/acme-logo-dark.png": _png(400, 120)}):
        assert visuals.client_logo(SITE, "Acme Foods Ltd") is not None
        assert visuals.client_logo(SITE, "Other Brand") is None


def test_an_image_too_large_to_be_a_logo_is_skipped():
    page = b'<header><img class="logo" src="/logo.png"></header>'
    with _site({SITE: page, "https://www.acme.in/logo.png": _png(3000, 1000)}):
        assert visuals.client_logo(SITE) is None


def test_wordpress_cropped_site_icons_are_skipped():
    page = b'<link rel="apple-touch-icon" href="/wp-content/uploads/cropped-acme-logo-180x180.png">'
    with _site({SITE: page,
                "https://www.acme.in/wp-content/uploads/cropped-acme-logo-180x180.png": _png(180, 180)}):
        assert visuals.client_logo(SITE) is None


def test_a_site_slower_than_the_deadline_gets_a_placeholder(monkeypatch):
    import threading

    release = threading.Event()
    monkeypatch.setattr(visuals, "_CLIENT_LOGO_DEADLINE", 0.05)
    with patch.object(visuals, "client_logo", side_effect=lambda *a: release.wait(5) and "data:x"), \
         patch.object(visuals, "ht_logo", return_value=None):
        deck = _deck()
        visuals.add_cover_logos(deck, "Acme", "https://slow.example")
    release.set()
    assert deck["slides"][0]["logos"][1]["image"] == "placeholder"


@pytest.mark.parametrize("url", ["", "acme.in", "ftp://acme.in/", "javascript:alert(1)"])
def test_a_non_http_site_is_never_fetched(url):
    with patch.object(visuals, "_get") as get:
        assert visuals.client_logo(url) is None
    get.assert_not_called()


def test_an_unreachable_site_is_none_not_an_error():
    with _site({}):
        assert visuals.client_logo(SITE) is None


def test_an_oversized_logo_is_skipped():
    big = _png(800, 300) + b"\x00" * (visuals._MAX_LOGO_BYTES + 10)
    page = b'<img class="logo" src="/logo.png">'
    with _site({SITE: page, "https://www.acme.in/logo.png": big}):
        assert visuals.client_logo(SITE) is None


# --- the cover ----------------------------------------------------------------


def _deck():
    return {"type": "deck", "slides": [
        {"layout": "title", "heading": "Acme festive"},
        {"layout": "image-hero", "heading": "Big idea",
         "image": "placeholder://16:9", "imageAlt": "Image placeholder (16:9): Families at a Diwali market"},
        {"layout": "two-column", "heading": "Canopy", "body": "x",
         "image": "placeholder://12:13", "imageAlt": "Image placeholder (12:13): A branded canopy in a Lucknow market"},
        {"layout": "closing", "heading": "Thanks"},
    ]}


def test_the_cover_gets_both_logos_with_placeholders_for_what_was_not_found():
    deck = _deck()
    with patch.object(visuals, "ht_logo", return_value=None), \
         patch.object(visuals, "client_logo", return_value=None):
        found = visuals.add_cover_logos(deck, "Acme", "https://acme.in")
    assert deck["slides"][0]["logos"] == [
        {"image": "placeholder", "alt": "HT Media logo"},
        {"image": "placeholder", "alt": "Acme logo"},
    ]
    assert found == {"ht_logo": False, "client_logo": False}


def test_logos_already_embedded_are_kept_without_refetching():
    deck = _deck()
    deck["slides"][0]["logos"] = [{"image": "data:image/png;base64,HT"},
                                  {"image": "data:image/png;base64,AC"}]
    with patch.object(visuals, "ht_logo") as ht, patch.object(visuals, "client_logo") as client:
        found = visuals.add_cover_logos(deck, "Acme", "https://acme.in")
    ht.assert_not_called()
    client.assert_not_called()
    assert [l["image"] for l in deck["slides"][0]["logos"]] == [
        "data:image/png;base64,HT", "data:image/png;base64,AC"]
    assert found == {"ht_logo": True, "client_logo": True}


def test_without_a_website_the_client_logo_is_not_looked_up():
    deck = _deck()
    with patch.object(visuals, "ht_logo", return_value="data:image/png;base64,HT"), \
         patch.object(visuals, "client_logo") as client:
        visuals.add_cover_logos(deck, "Acme", "")
    client.assert_not_called()
    assert deck["slides"][0]["logos"][1]["image"] == "placeholder"


def test_a_deck_without_a_title_cover_is_left_alone():
    deck = {"slides": [{"layout": "quote", "quote": "x"}]}
    assert visuals.add_cover_logos(deck, "Acme", "") == {"ht_logo": False, "client_logo": False}
    assert "logos" not in deck["slides"][0]


def test_a_drive_failure_is_not_cached_so_the_next_deck_retries(monkeypatch):
    monkeypatch.setenv("HT_ASSETS_FOLDER_ID", "folder")
    with patch("googleapiclient.discovery.build", side_effect=OSError("blip")), \
         patch("agents.solutioning_agent.oauth_creds.get_credentials"):
        assert visuals.ht_logo() is None
    drive = MagicMock()
    drive.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "1", "name": "HT logo.png", "mimeType": "image/png"}]}
    drive.files.return_value.get_media.return_value.execute.return_value = b"PNGBYTES"
    with patch("googleapiclient.discovery.build", return_value=drive), \
         patch("agents.solutioning_agent.oauth_creds.get_credentials"):
        assert visuals.ht_logo() == "data:image/png;base64," + base64.b64encode(b"PNGBYTES").decode()


# --- generated images -----------------------------------------------------------


def _imagen_returning(data=b"JPEGBYTES", error=None):
    """Stands in for gemini-2.5-flash-image's generate_content response."""
    client_cls = patch("google.genai.Client")
    started = client_cls.start()
    gen = started.return_value.models.generate_content
    if error:
        gen.side_effect = error
    else:
        part = MagicMock()
        part.inline_data.data = data
        resp = MagicMock()
        resp.candidates = [MagicMock(content=MagicMock(parts=[part] if data is not None else []))]
        gen.return_value = resp
    return client_cls, gen


def test_each_placeholder_is_generated_from_its_description_at_its_slot_ratio():
    deck = _deck()
    patcher, gen = _imagen_returning()
    try:
        result = visuals.fill_images(deck)
    finally:
        patcher.stop()
    assert result == {"generated": 2, "placeholders": 0}
    hero, two_col = deck["slides"][1], deck["slides"][2]
    assert hero["image"].startswith("data:image/jpeg;base64,")
    assert hero["imageAlt"] == "Families at a Diwali market"
    calls = {c.kwargs["config"].image_config.aspect_ratio: c.kwargs["contents"]
             for c in gen.call_args_list}
    assert set(calls) == {"16:9", "1:1"}  # 12:13 takes a square, cropped to fill
    assert calls["16:9"].startswith("Families at a Diwali market")
    assert two_col["image"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("data, error", [(None, None), (b"", None), (None, RuntimeError("quota"))])
def test_a_filtered_or_failed_generation_leaves_the_captioned_placeholder(data, error):
    deck = _deck()
    patcher, _ = _imagen_returning(data=data, error=error)
    try:
        result = visuals.fill_images(deck)
    finally:
        patcher.stop()
    assert result == {"generated": 0, "placeholders": 2}
    assert deck["slides"][1]["image"] == "placeholder://16:9"
    assert deck["slides"][1]["imageAlt"].startswith("Image placeholder (16:9): ")


def test_a_resubmitted_deck_does_not_pay_for_the_same_image_twice():
    patcher, gen = _imagen_returning()
    try:
        visuals.fill_images(_deck())
        visuals.fill_images(_deck())
    finally:
        patcher.stop()
    assert gen.call_count == 2  # two slots, once each


def test_why_ht_logo_slots_and_undescribed_slots_are_never_generated():
    deck = _deck()
    deck["slides"][1]["notes"] = "[why-ht:opener] Source deck: x."
    deck["slides"][2]["imageAlt"] = f"Image placeholder (12:13): {master_deck.DEFAULT_CAPTION}"
    with patch.object(visuals, "generate_image") as gen:
        assert visuals.fill_images(deck) == {"generated": 0, "placeholders": 0}
    gen.assert_not_called()


def test_real_images_are_left_alone():
    deck = _deck()
    deck["slides"][1]["image"] = "data:image/png;base64,REAL"
    with patch.object(visuals, "generate_image", return_value="data:image/jpeg;base64,NEW") as gen:
        visuals.fill_images(deck)
    assert deck["slides"][1]["image"] == "data:image/png;base64,REAL"
    assert gen.call_count == 1


def test_images_past_the_storage_budget_stay_placeholders(monkeypatch):
    monkeypatch.setattr(visuals, "_IMAGE_BUDGET_CHARS", len(json.dumps(_deck())) + 150)
    deck = _deck()
    with patch.object(visuals, "generate_image", return_value="data:image/jpeg;base64," + "A" * 100):
        assert visuals.fill_images(deck) == {"generated": 1, "placeholders": 1}


# --- wired into the deck tools ----------------------------------------------------


def _spine_deck():
    return {
        "type": "deck",
        "slides": [
            {"layout": "title", "eyebrow": "HT Media × Acme", "heading": "Acme festive"},
            {"layout": "two-column", "heading": "The brief", "body": "Drive trial.", "aside": "In 8 weeks"},
            {"layout": "image-hero", "heading": "The idea", "image": "placeholder",
             "imageAlt": "A crowded weekly haat at dusk"},
            {"layout": "quote", "quote": "Make the sale an event."},
            {"layout": "timeline", "steps": [{"title": "Tease"}, {"title": "Launch"}, {"title": "Sustain"}]},
            {"layout": "two-column", "heading": "Pillar 1", "body": "x", "image": "data:image/jpeg;base64,A"},
            {"layout": "two-column", "heading": "Pillar 2", "body": "y", "image": "data:image/jpeg;base64,B"},
            {"layout": "two-column", "heading": "Next steps", "body": "Costing from HT's pricing team.",
             "aside": "Commercials from HT's pricing team."},
            {"layout": "closing", "heading": "Thank you"},
        ],
    }


def test_build_renders_the_deck_with_its_logos_and_images_filled_and_reports_them():
    rendered = {}
    with patch.object(visuals, "ht_logo", return_value="data:image/png;base64,HT"), \
         patch.object(visuals, "client_logo", return_value=None) as client, \
         patch.object(visuals, "generate_image", return_value="data:image/jpeg;base64,IMG"), \
         patch.object(deck_tools, "_render_pptx", side_effect=lambda j: rendered.update(deck=json.loads(j)) or b"PPTX"), \
         patch.object(deck_tools, "_upload_pptx", return_value={"id": "f1", "webViewLink": "https://docs.google.com/presentation/d/f1/edit"}), \
         patch.object(deck_tools, "_save_brief") as save:
        result = deck_tools.build_solution_deck(json.dumps(_spine_deck()), "Acme", "b1", "https://acme.in")

    client.assert_called_once_with("https://acme.in", "Acme")
    assert result["ht_logo"] is True and result["client_logo"] is False
    assert result["images"] == {"generated": 1, "placeholders": 0}
    deck = rendered["deck"]
    assert deck["slides"][0]["logos"][0]["image"] == "data:image/png;base64,HT"
    assert deck["slides"][2]["image"] == "data:image/jpeg;base64,IMG"
    # What's stored is what was rendered, so a later edit doesn't regenerate.
    assert json.loads(save.call_args.kwargs["deck_json"]) == deck


def test_a_deck_breaking_the_rules_generates_nothing():
    deck = _spine_deck()
    deck["slides"][3]["quote"] = "x" * 400
    with patch.object(visuals, "generate_image") as gen, \
         patch.object(deck_tools, "_render_pptx") as render:
        result = deck_tools.build_solution_deck(json.dumps(deck), "Acme", "b1", "")
    assert "problems" in result
    gen.assert_not_called()
    render.assert_not_called()


def test_a_revision_never_looks_up_logos_or_generates_images():
    """A revision changes exactly the fields it names; pictures came with the build."""
    old = _spine_deck()
    master_deck.enforce(old)
    old["slides"][2]["image"] = "data:image/jpeg;base64,KEPT"
    old["slides"][4]["layout"] = "two-column"
    old["slides"][4].update(body="x", image="placeholder://12:13", imageAlt="Image placeholder (12:13): a van")
    stored = {"deck_json": json.dumps(old), "deck_file_id": "f1", "deck_link": "L", "client_name": "Acme"}
    saved = {}
    with patch.object(deck_tools, "_load_brief", return_value=stored), \
         patch.object(visuals, "ht_logo") as ht, \
         patch.object(visuals, "generate_image") as gen, \
         patch.object(deck_tools, "_render_pptx", return_value=b"PPTX"), \
         patch.object(deck_tools, "_upload_pptx"), \
         patch.object(deck_tools, "_save_brief", side_effect=lambda bid, **kw: saved.update(kw)):
        result = deck_tools.update_deck("b1", json.dumps([{"slide_index": 3, "field": "quote", "value": "New line."}]))

    assert result["edits_applied"] == ["slide 3: quote"]
    ht.assert_not_called()
    gen.assert_not_called()
    deck = json.loads(saved["deck_json"])
    assert deck["slides"][2]["image"] == "data:image/jpeg;base64,KEPT"
    assert "logos" not in deck["slides"][0]


def test_the_build_tool_declares_client_website_for_the_model():
    from google.adk.tools import FunctionTool

    schema = FunctionTool(deck_tools.build_solution_deck)._get_declaration().parameters_json_schema
    assert {"deck_json", "client_name", "brief_id", "client_website"} <= set(schema["properties"])
    # Optional, so existing callers and a model that omits it still work.
    assert "client_website" not in schema["required"]


def test_an_unexpected_failure_adding_pictures_still_publishes_the_deck():
    with patch.object(visuals, "add_cover_logos", side_effect=KeyError("bug")), \
         patch.object(deck_tools, "_render_pptx", return_value=b"PPTX") as render, \
         patch.object(deck_tools, "_upload_pptx", return_value={"id": "f1", "webViewLink": "L"}), \
         patch.object(deck_tools, "_save_brief"):
        result = deck_tools.build_solution_deck(json.dumps(_spine_deck()), "Acme", "b1", "")
    render.assert_called_once()
    assert result["link"] == "L"
    assert "pictures_error" in result


def test_a_generated_picture_carries_the_representation_note():
    deck = _deck()
    with patch.object(visuals, "generate_image", return_value="data:image/jpeg;base64,NEW"):
        visuals.fill_images(deck)
    pictured = [s for s in deck["slides"] if s.get("image") == "data:image/jpeg;base64,NEW"]
    assert pictured and all(s["disclaimer"] == visuals.GENERATED_NOTE for s in pictured)
