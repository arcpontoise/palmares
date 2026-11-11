// Audience measurement of the sites of the Compagnie d'archers de Pontoise,
// with Matomo on stats.arcpontoise.fr, set up for the CNIL consent exemption
// (anonymous statistics only, see the README of arcpontoise/stats).
//
// Reference copy: each site serves its own copy of this file, loaded with
//     <script src="/js/stats.js" data-site="2" defer></script>
// data-site is the Matomo id of the site; every page view is also counted
// in the « Ensemble des sites » (id 1). Optional attributes, to keep
// personal data and secrets out of the statistics:
//     data-titre="Fiche d'un archer"   title recorded instead of the page's
//     data-adresse="fiche.html"        address recorded instead of the page's
//     data-sans-parametres             query string and fragment dropped
//
// An element <button class="stats-refus" data-stats-refus hidden> on the
// page becomes the opt-out switch (shown only once the tracker is loaded).

(() => {
  "use strict";

  const HOST = "https://stats.arcpontoise.fr/";
  const ALL_SITES = 1;
  // Only the published sites are measured, never a local preview
  const MEASURED = /(^|\.)arcpontoise\.fr$|^arcpontoise\.github\.io$/;
  // 13 months at most for the visitor cookie (CNIL), not extended on visits
  const COOKIE_SECONDS = 390 * 24 * 3600;
  const LABEL_MEASURED = "Refuser la mesure d'audience";
  const LABEL_REFUSED = "Mesure d'audience refusée : l'accepter";

  const script = document.currentScript;
  const options = script?.dataset ?? {};
  const site = Number(options.site);
  if (!Number.isInteger(site) || site < 1 || !MEASURED.test(window.location.hostname)) {
    return;
  }

  // matomo.js replaces window._paq once loaded: always push through it
  const push = (command) => (window._paq = window._paq || []).push(command);
  push(["setTrackerUrl", `${HOST}matomo.php`]);
  push(["setSiteId", String(site)]);
  push(["addTracker", `${HOST}matomo.php`, String(ALL_SITES)]);
  push(["setVisitorCookieTimeout", COOKIE_SECONDS]);

  if (options.adresse || "sansParametres" in options) {
    // A relative data-adresse is resolved against the page, like a link
    const url = new URL(options.adresse || window.location.pathname, window.location.href);
    if (!options.adresse) url.search = "";
    url.hash = "";
    push(["setCustomUrl", url.href]);
  }
  if (options.titre) {
    push(["setDocumentTitle", options.titre]);
  }

  push(["enableHeartBeatTimer"]);
  push(["trackPageView"]);
  push(["enableLinkTracking"]);

  function optOutSwitch() {
    const button = document.querySelector("[data-stats-refus]");
    if (!button) return;
    push([
      function show() {
        // Run once per tracker: bind the switch on the first one only
        if (button.dataset.pret) return;
        button.dataset.pret = "oui";
        const tracker = this;
        const label = () => {
          button.textContent = tracker.isUserOptedOut() ? LABEL_REFUSED : LABEL_MEASURED;
        };
        button.addEventListener("click", () => {
          push([tracker.isUserOptedOut() ? "forgetUserOptOut" : "optUserOut"]);
          label();
        });
        label();
        button.hidden = false;
      },
    ]);
  }

  const loader = document.createElement("script");
  loader.src = `${HOST}matomo.js`;
  loader.async = true;
  document.head.append(loader);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", optOutSwitch);
  } else {
    optOutSwitch();
  }
})();
