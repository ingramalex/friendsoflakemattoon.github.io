// @ts-check
const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 15000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5500',
    headless: true,
  },
  webServer: {
    command: 'npx serve . -p 5500 -s',
    port: 5500,
    reuseExistingServer: true,
    timeout: 10000,
  },
  projects: [
    { name: 'Desktop Chrome', use: { ...devices['Desktop Chrome'] } },
    { name: 'Mobile Chrome',  use: { ...devices['Pixel 5'] } },
  ],
});
