const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ request }) => {
  const response = await request.post('http://127.0.0.1:8000/api/reset');
  expect(response.ok()).toBeTruthy();
});

test('compra, falla, evidencia y recuperación', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByText('Sistema conectado')).toBeVisible();
  await expect(page.getByTestId('stock-neon')).toHaveText('200');
  await page.screenshot({ path: '../.local/screenshots/boleteria.png', fullPage: true });
  await page.getByRole('button', { name: 'Compra exitosa' }).click();
  await expect(page.locator('.saga-panel > .section-heading .badge')).toHaveText('Confirmada', { timeout: 20000 });
  await expect(page.getByTestId('stock-neon')).toHaveText('199');
  await expect(page.locator('.evidence')).toContainText('Cobrado');
  await page.getByRole('button', { name: 'Restablecer demo' }).click();
  await page.getByRole('button', { name: 'Restablecer datos' }).click();
  await expect(page.getByTestId('stock-neon')).toHaveText('200');
  await page.getByRole('button', { name: 'Fallar confirmación' }).click();
  await expect(page.locator('.saga-panel > .section-heading .badge')).toHaveText('Compensada', { timeout: 20000 });
  await expect(page.locator('.evidence')).toContainText('Reembolsado');
  await expect(page.getByTestId('stock-neon')).toHaveText('200');
  await page.screenshot({ path: '../.local/screenshots/compensacion.png', fullPage: true });
  await page.getByRole('button', { name: 'Opciones del experimento' }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Fallar confirmación' }).click();
  await expect(page.locator('.saga-panel > .section-heading .badge')).toHaveText('Requiere reintento', { timeout: 20000 });
  await page.getByRole('button', { name: 'Reintentar compensación' }).click();
  await expect(page.locator('.saga-panel > .section-heading .badge')).toHaveText('Compensada', { timeout: 20000 });
  await expect(page.getByTestId('stock-neon')).toHaveText('200');
  expect(errors).toEqual([]);
});

test('laboratorio limita pagos y conserva el catálogo', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Sistema conectado')).toBeVisible();
  await page.getByRole('button', { name: /Laboratorio/ }).click();
  await page.getByRole('button', { name: 'Saturar pagos' }).click();
  await expect(page.getByText('Ejecución finalizada')).toBeVisible({ timeout: 60000 });
  await expect(page.locator('.checks .fail')).toHaveCount(0);
  await expect(page.locator('.checks .pass')).toHaveCount(5);
  await page.screenshot({ path: '../.local/screenshots/laboratorio.png', fullPage: true });
});

test('pantalla móvil sin desborde horizontal', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByText('Sistema conectado')).toBeAttached();
  await expect(page.getByRole('button', { name: 'Compra exitosa' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: '../.local/screenshots/mobile.png', fullPage: true });
  await page.getByRole('button', { name: /Laboratorio/ }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
