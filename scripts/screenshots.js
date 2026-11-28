// Regenerate the README screenshots:
//   DATA_DIR=/tmp/demo python scripts/seed_demo.py
//   DATA_DIR=/tmp/demo SCHEDULER=0 gunicorn -b 127.0.0.1:8123 wsgi:app &
//   node scripts/screenshots.js http://127.0.0.1:8123 docs/screenshots
const { chromium } = require('playwright');

const [base = 'http://127.0.0.1:8123', out = 'docs/screenshots'] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1, colorScheme: 'light' });
  const p = await ctx.newPage();
  const errors = [];
  p.on('pageerror', e => errors.push(e.message));
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

  await p.goto(base + '/login');
  await p.fill('input[name=username]', 'admin');
  await p.fill('input[name=password]', 'demo password 1');
  await Promise.all([p.waitForNavigation(), p.click('button.primary')]);

  const shot = async (url, name, opts = {}) => {
    await p.goto(base + url, { waitUntil: 'networkidle' });
    // full-page captures would repeat the sticky header in the middle of the image
    if (opts.full) await p.addStyleTag({ content: '.topbar { position: static !important; }' });
    if (opts.before) await opts.before(p);
    await p.waitForLoadState('networkidle');
    await p.screenshot({ path: `${out}/${name}.png`, fullPage: opts.full ?? false, clip: opts.clip });
    console.log('saved', name);
  };
  const first = async (sel) => (await p.locator(sel).first().getAttribute('href'));

  await shot('/', 'dashboard', { full: true });
  await shot('/items/', 'inventory');
  await p.goto(base + '/items/?q=Projector');
  const itemUrl = await first('table a[href^="/items/"]');
  await shot(itemUrl, 'item', { full: true, before: async pg => { await pg.click('details.history summary'); } });
  await p.goto(base + '/events/');
  await shot('/events/', 'events');
  await p.goto(base + '/events/?q=Advent+concert');
  const evUrl = await first('table a[href^="/events/"]');
  await shot(evUrl, 'event', { full: true });
  await shot('/reservations/', 'reservations');
  // clash warning while booking
  await shot('/reservations/new', 'reservation-clash', { full: true, before: async pg => {
    const v = await pg.locator('select[name=item_id] option', { hasText: 'Projector' }).getAttribute('value');
    await pg.selectOption('select[name=item_id]', v);
    const concert = await pg.locator('select[name=event_id] option', { hasText: 'Advent concert' }).textContent();
    const day = concert.trim().split(' ')[0].split('.').reverse().join('-');
    await pg.fill('input[name=start_at]', `${day}T16:00`);
    await pg.fill('input[name=end_at]', `${day}T19:00`);
    await pg.locator('input[name=end_at]').dispatchEvent('change');
    await pg.waitForSelector('#availability p');
    await pg.locator('textarea[name=purpose]').fill('Film night for the youth group');
    await pg.locator('h1').click();
  } });
  await shot('/items/?kind=space', 'rooms');
  await shot('/maintenance/', 'maintenance', { full: true });
  await p.goto(base + '/reports/');
  await shot('/reports/assets', 'assets-report', { full: true });
  await shot('/audit/', 'audit-log');
  await shot('/people/', 'people');
  await p.goto(base + '/profile', { waitUntil: 'networkidle' });
  await p.locator('details summary', { hasText: 'How to add it' }).click();
  await p.locator('section.card', { has: p.locator('h2', { hasText: 'Calendar' }) }).screenshot({ path: `${out}/calendar.png` });
  console.log('saved calendar');
  await p.goto(base + '/items/');
  await shot('/items/labels?' + (await p.locator('input[name=id]').evaluateAll(els => els.slice(0, 6).map(e => 'id=' + e.value).join('&'))), 'qr-labels', { clip: { x: 0, y: 0, width: 1280, height: 380 } });

  // Slovak + mobile
  const sk = await ctx.newPage();
  await sk.goto(base + '/items/');
  await sk.locator('footer button.linkbutton').click();
  await sk.waitForLoadState('networkidle');
  await sk.screenshot({ path: `${out}/inventory-sk.png` });
  console.log('saved inventory-sk');
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: 'dark' });
  await mobile.addCookies(await ctx.cookies());
  const m = await mobile.newPage();
  await m.goto(base + '/', { waitUntil: 'networkidle' });
  await m.screenshot({ path: `${out}/mobile-dark.png` });
  console.log('saved mobile-dark');
  // switch language back
  await sk.locator('footer button.linkbutton').click();

  await browser.close();
  if (errors.length) { console.error('Browser errors:', errors); process.exit(1); }
})();
