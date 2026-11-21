// Journeys dump for analyze.py. Read-only GETs with the console's own session cookie.
// Use A: paste in the DevTools console of https://projectnandaitaly.ai/team (logged in);
//        it downloads journeys-dump.json.
// Use B: `edge js "$(cat tools/journeys/dump.js)"` then `edge js 'window.__jdump'` (see README).
// No token or cookie is read or stored: fetch uses credentials:"include" like the page does.
(() => {
  const get = (p) => fetch(p, { credentials: "include" }).then((r) => {
    if (!r.ok) throw new Error(p + " " + r.status);
    return r.json();
  });
  window.__jdump = null;
  window.__jdumpErr = null;
  (async () => {
    const list = await get("/v1/team/journeys");
    const assignment = await get("/v1/team/assignment").catch(() => ({}));
    const details = {};
    for (const j of list.journeys || []) {
      try { details[j.need_id] = await get("/v1/team/journeys/" + encodeURIComponent(j.need_id)); }
      catch (e) { /* skip one bad detail */ }
    }
    const dump = { list, details, assignment, pulled_at: new Date().toISOString() };
    window.__jdump = JSON.stringify(dump);
    if (!window.__jdumpNoDownload) {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([window.__jdump], { type: "application/json" }));
      a.download = "journeys-dump.json";
      a.click();
    }
  })().catch((e) => { window.__jdumpErr = String(e); });
  return "started";
})();
