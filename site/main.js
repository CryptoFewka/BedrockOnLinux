/* BedrockOnLinux site behaviour.
   One job: point the download buttons at the current release and keep the
   install commands naming the real files. Progressive: with JavaScript off,
   every button already links to the releases page and the commands name the
   release this page was built for. The system picker is pure CSS. */

(function () {
  "use strict";

  var API = "https://api.github.com/repos/Wyze3306/BedrockOnLinux/releases/latest";
  var CACHE_KEY = "bol-latest-release";

  /* The cache exists to paint instantly and to survive a rate-limited or
     offline API, never to hide a release that just shipped. So it is not a
     gate: a stored answer is shown right away and then revalidated. This
     floor only stops a burst of reloads from spending the visitor's
     unauthenticated API budget. */
  var REVALIDATE_MS = 5 * 60 * 1000;

  var PATTERNS = {
    appimage: /\.AppImage$/i,
    deb: /\.deb$/i,
    rpm: /\.rpm$/i,
    flatpak: /\.flatpak$/i,
    pyz: /\.pyz$/i
  };

  function size(bytes) {
    if (bytes < 1048576) return Math.round(bytes / 1024) + " KB";
    return (bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0) + " MB";
  }

  function apply(release) {
    if (!release || !release.assets) return;

    var found = {};
    release.assets.forEach(function (asset) {
      Object.keys(PATTERNS).forEach(function (kind) {
        if (!found[kind] && PATTERNS[kind].test(asset.name)) found[kind] = asset;
      });
    });

    /* Download buttons: real URL, real size. */
    document.querySelectorAll("[data-asset]").forEach(function (link) {
      var asset = found[link.getAttribute("data-asset")];
      if (!asset) return;
      link.href = asset.browser_download_url;
      var label = link.querySelector(".btn-size");
      if (label) label.textContent = size(asset.size);
    });

    /* Install commands: name the file the visitor is about to download. */
    document.querySelectorAll("[data-file]").forEach(function (slot) {
      var asset = found[slot.getAttribute("data-file")];
      if (asset) slot.textContent = asset.name;
    });

    var version = (release.tag_name || "").replace(/^v/, "");
    var tag = document.getElementById("ver");
    if (tag && version) tag.textContent = ", v" + version;

    var main = document.getElementById("dl-main");
    var sub = document.getElementById("dl-main-sub");
    if (main && found.appimage) main.href = found.appimage.browser_download_url;
    if (sub && found.appimage) {
      sub.textContent = "AppImage, " + size(found.appimage.size) +
        (version ? ", v" + version : "");
    }
  }

  function load() {
    var cached = null;
    try {
      cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    } catch (e) { /* no cache, ask the API */ }

    /* Show what we already know, whatever its age. */
    if (cached && cached.release) apply(cached.release);

    /* Then go and look for a newer one, unless we just did. */
    if (cached && Date.now() - cached.at < REVALIDATE_MS) return;

    fetch(API, { headers: { Accept: "application/vnd.github+json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (release) {
        apply(release);
        try {
          localStorage.setItem(CACHE_KEY,
            JSON.stringify({ at: Date.now(), release: release }));
        } catch (e) { /* private mode: fine, we just re-fetch next time */ }
      })
      .catch(function () { /* links already point at /releases/latest */ });
  }

  load();
})();
