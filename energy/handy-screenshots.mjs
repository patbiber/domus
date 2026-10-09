// Puppeteer: drei Handy-Ansichten der Kunden-Oberfläche aufnehmen (aufgerufen von handy-screenshots.py).
// homi wird über einen lokalen Zwischen-Server (localhost:18099, Host biber.homi.solar) geladen – ohne Passwort.
import puppeteer from "puppeteer";
const browser = await puppeteer.launch({ args: ["--no-sandbox"] });
const page = await browser.newPage();
await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
await page.goto("http://localhost:18099/", { waitUntil: "networkidle2" });
await new Promise(r => setTimeout(r, 4000));
for (const [tab, datei] of [["uebersicht", "app-uebersicht.png"], ["verlauf", "app-verlauf.png"], ["preise", "app-preise.png"]]) {
  await page.click(`.tabs button[data-tab=${tab}]`);
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: `/w/${datei}` });
}
await browser.close();
