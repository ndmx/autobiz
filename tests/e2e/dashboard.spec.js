import { expect, test } from "@playwright/test";

const baseURL = process.env.BASE_URL || "http://127.0.0.1:7864";

test("dashboard loads listings and filters without layout breakage", async ({ page }) => {
  await page.goto(`${baseURL}/dashboard`);
  await expect(page.getByText("Philadelphia-first acquisition board")).toBeVisible();
  await expect(page.getByRole("button", { name: "Scrape PA Listings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Score PA Data" })).toBeVisible();
  await expect(page.locator("table")).toBeVisible();
  await expect(page.locator("#visible-count")).toContainText("shown");

  await page.getByLabel("Search").fill("zzzz-no-match");
  await expect(page.locator("#visible-count")).toContainText("0 shown");
  await page.getByLabel("Search").fill("");
  await expect(page.locator("#visible-count")).not.toContainText("0 shown");
});

test("settings navigation works from dashboard", async ({ page }) => {
  await page.goto(`${baseURL}/dashboard`);
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByText("Scoring Model")).toBeVisible();
});
