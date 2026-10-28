// Rendert homi-film.html Bild für Bild als PNG (läuft im Puppeteer-Container, siehe render.sh).
// STILLS=0,450,900 rendert nur diese Bilder (Vorschau).
const fs = require("fs"), puppeteer = require("puppeteer");
(async () => {
  const out = process.env.OUT || "/film/out/frames";
  fs.mkdirSync(out, { recursive: true });
  const b = await puppeteer.launch({ args: ["--no-sandbox", "--allow-file-access-from-files"] });
  const p = await b.newPage();
  await p.goto("file:///film/homi-film.html?render");
  const N = await p.evaluate(() => FILM.N);
  const nur = process.env.STILLS ? process.env.STILLS.split(",").map(Number) : null;
  const liste = nur || [...Array(N).keys()];
  for (const i of liste) {
    const d = await p.evaluate(i => { FILM.render(i); return document.getElementById("out").toDataURL("image/png").slice(22); }, i);
    fs.writeFileSync(`${out}/f${String(i).padStart(5, "0")}.png`, Buffer.from(d, "base64"));
    if (!nur && i % 300 === 0) console.log(`Bild ${i}/${N}`);
  }
  console.log(JSON.stringify(await p.evaluate(() => ({ TAG: FILM.TAG, EV: FILM.EV, LOG: FILM.LOG }))));
  await b.close();
})();
