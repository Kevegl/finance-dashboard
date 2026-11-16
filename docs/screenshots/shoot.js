// Nimmt die README-Screenshots auf (Playwright). Daten: docs/screenshots/demo_data.py (nur erfundene Werte).
//   node docs/screenshots/shoot.js <URL> <Zielordner> <Import-CSV> [server|local]
// «local»: Server mit FD_LOCAL=1 gestartet -> nur die Einstellungen (Programm + Updates) werden aufgenommen.
const { chromium } = require("playwright");

(async () => {
  const [B, out, csv, mode = "server"] = process.argv.slice(2);
  const browser = await chromium.launch();
  const login = async (ctx) => ctx.request.post(B + "/api/login",
    { data: { username: "demo", password: "demo-passwort" }, headers: { Origin: B } });
  const page = async (opt) => {
    const ctx = await browser.newContext({ deviceScaleFactor: 1.5, ...opt });
    await login(ctx);
    return ctx.newPage();
  };
  const settle = (p, ms = 1800) => p.waitForTimeout(ms);

  if (mode === "local") {
    const p = await page({ viewport: { width: 1000, height: 900 }, colorScheme: "light" });
    await p.goto(B + "/#/einstellungen"); await settle(p, 4000);
    const a = await p.locator("#localCard").boundingBox(), u = await p.locator("#updCard").boundingBox();
    await p.evaluate((y) => window.scrollTo(0, y - 100), a.y);   // unter der fixen Kopfzeile
    await settle(p, 500);
    const a2 = await p.locator("#localCard").boundingBox(), u2 = await p.locator("#updCard").boundingBox();
    await p.screenshot({ path: `${out}/einstellungen.png`,
      clip: { x: a2.x - 16, y: a2.y - 16, width: a2.width + 32, height: u2.y + u2.height - a2.y + 32 } });
    await browser.close();
    return;
  }

  for (const scheme of ["light", "dark"]) {
    const p = await page({ viewport: { width: 1280, height: 1110 }, colorScheme: scheme });
    await p.goto(B + "/"); await settle(p);
    await p.screenshot({ path: `${out}/dashboard-${scheme === "light" ? "hell" : "dunkel"}.png` });
    if (scheme === "light") {
      await p.setViewportSize({ width: 1280, height: 760 });
      await p.click("text=Säule 3a"); await settle(p);
      await p.screenshot({ path: `${out}/saeule-3a.png` });
      await p.click("text=Depot"); await settle(p, 800);
      await p.setViewportSize({ width: 1280, height: 860 });
      await p.click("#btnImport"); await settle(p, 500);
      await p.setInputFiles("#impFile", csv); await settle(p);
      await p.locator("#dlgImport").screenshot({ path: `${out}/import.png` });
      await p.keyboard.press("Escape");
      await p.setViewportSize({ width: 1280, height: 1150 });
      await p.goto(B + "/#/vorsorge"); await settle(p, 2500);
      await p.screenshot({ path: `${out}/spartipps.png` });
    }
  }
  const m = await page({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    colorScheme: "dark" });
  await m.goto(B + "/"); await settle(m);
  await m.screenshot({ path: `${out}/handy.png` });
  await browser.close();
})();
