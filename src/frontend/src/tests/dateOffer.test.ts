// @vitest-environment happy-dom
// SPEC-UI-POLISH-PASS §7.4 (T-U5): the one-time date-format offer. Only a
// browser that looks American sees it, once; any answer is saved with the
// account's preferences, and a typed date then follows the chosen order.
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ queue: vi.fn() }));
vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  queuePreferences: api.queue,
}));

import appSource from "../App.vue?raw";
import DateFormatOffer from "../components/DateFormatOffer.vue";
import PartialDateInput from "../components/PartialDateInput.vue";
import { DEFAULT_PREFERENCES } from "../services/preferences";
import { preferences, dateOfferHiddenFor } from "../state/preferences";
import { currentUser } from "../state/auth";
import {
  isUsTimeZone,
  looksAmerican,
  shouldOfferDateFormat,
} from "../utils/dates";

const Modal = defineComponent({
  props: ["title", "description", "dismissible"],
  emits: ["close"],
  setup(props, { slots, emit }) {
    return () =>
      h("div", { class: "modal" }, [
        h("h2", props.title),
        h("button", { class: "close", onClick: () => emit("close") }, "×"),
        slots.default?.(),
      ]);
  },
});

beforeEach(() => {
  vi.resetAllMocks();
  currentUser.value = { id: "member" } as typeof currentUser.value;
  preferences.value = { ...DEFAULT_PREFERENCES, ui_welcome_completed: true };
  api.queue.mockImplementation(async (changes) => ({
    prefs: { ...preferences.value, ...changes },
    latest: true,
  }));
});
afterEach(() => {
  preferences.value = { ...DEFAULT_PREFERENCES };
  dateOfferHiddenFor.value = null;
  currentUser.value = null;
});

describe("who is asked", () => {
  it.each([
    ["en-US", "Europe/London", true],
    ["en-US", "America/Chicago", true],
    ["en-GB", "America/Chicago", true],
    ["fr-FR", "America/Indiana/Indianapolis", true],
    ["en-GB", "Pacific/Honolulu", true],
    ["en-GB", "Europe/London", false],
    ["en-CA", "America/Toronto", false],
    ["es-MX", "America/Mexico_City", false],
    ["pt-BR", "America/Sao_Paulo", false],
    ["en", "Asia/Tokyo", false],
  ])("%s in %s: %s", (language, zone, expected) => {
    expect(looksAmerican(language, zone)).toBe(expected);
  });

  it("knows the US zones but not every America/* zone", () => {
    expect(isUsTimeZone("America/Kentucky/Louisville")).toBe(true);
    expect(isUsTimeZone("America/North_Dakota/Center")).toBe(true);
    expect(isUsTimeZone("America/Vancouver")).toBe(false);
  });

  it("asks once, never after a choice, and not over the welcome dialog", () => {
    const us = { american: () => true };
    const base = { ...DEFAULT_PREFERENCES, ui_welcome_completed: true };
    expect(shouldOfferDateFormat(base, us)).toBe(true);
    expect(shouldOfferDateFormat(base, { american: () => false })).toBe(false);
    expect(
      shouldOfferDateFormat({ ...base, date_format_offered: true }, us),
    ).toBe(false);
    expect(
      shouldOfferDateFormat({ ...base, date_format_chosen: true }, us),
    ).toBe(false);
    expect(
      shouldOfferDateFormat({ ...base, ui_welcome_completed: false }, us),
    ).toBe(false);
  });

  it("waits while the quick tour runs, and once closed this session", () => {
    const base = { ...DEFAULT_PREFERENCES, ui_welcome_completed: true };
    const american = () => true;
    // "Save & take a tour" finishes the welcome dialog and starts the tour
    // in the same moment; the offer must not open over the tour
    expect(shouldOfferDateFormat(base, { american, tourActive: true })).toBe(
      false,
    );
    expect(shouldOfferDateFormat(base, { american, tourActive: false })).toBe(
      true,
    );
    expect(shouldOfferDateFormat(base, { american, hidden: true })).toBe(false);
  });

  it("is wired to the tour and the session hide in App.vue", () => {
    const gate = appSource.slice(appSource.indexOf("<DateFormatOffer"));
    expect(gate).toMatch(/tourActive:\s*quickTourActive/);
    expect(gate).toMatch(/hidden:\s*dateOfferHiddenFor === currentUser\.id/);
  });
});

describe("the dialog", () => {
  const open = () =>
    mount(DateFormatOffer, { global: { stubs: { UiModal: Modal } } });

  it("offers the four formats, month first, with a note on typed dates", () => {
    const wrapper = open();
    const options = wrapper
      .findAll(".offer-option strong")
      .map((o) => o.text());
    expect(options).toEqual([
      "03/14/2011",
      "14 March 2011",
      "14/03/2011",
      "2011-03-14",
    ]);
    expect(wrapper.find(".offer-note").text()).toMatch(
      /easier to misread month first/,
    );
  });

  it("saves the chosen format as an explicit choice and as answered", async () => {
    const wrapper = open();
    await wrapper.find('[data-style="mdy"]').trigger("click");
    await flushPromises();
    expect(api.queue).toHaveBeenCalledWith({
      date_format: "mdy",
      date_format_chosen: true,
      date_format_offered: true,
    });
    expect(preferences.value.date_format).toBe("mdy");
    expect(
      shouldOfferDateFormat(preferences.value, { american: () => true }),
    ).toBe(false);
  });

  it("closing keeps day/month/year and never asks again", async () => {
    const wrapper = open();
    await wrapper.find(".close").trigger("click");
    await flushPromises();
    expect(api.queue).toHaveBeenCalledWith({ date_format_offered: true });
    expect(preferences.value.date_format).toBe("dmy");
    expect(
      shouldOfferDateFormat(preferences.value, { american: () => true }),
    ).toBe(false);
  });

  it("closing hides it at once, even when saving fails", async () => {
    api.queue.mockRejectedValueOnce(new Error("Failed to fetch"));
    const wrapper = open();
    await wrapper.find(".close").trigger("click");
    expect(dateOfferHiddenFor.value).toBe("member");
    await flushPromises();
    // not saved, so a later visit offers it again; this session it is gone
    expect(preferences.value.date_format_offered).toBe(false);
    expect(
      shouldOfferDateFormat(preferences.value, {
        american: () => true,
        hidden: dateOfferHiddenFor.value === "member",
      }),
    ).toBe(false);
  });

  it("shows the error and stays open when saving a choice fails", async () => {
    api.queue.mockRejectedValueOnce(
      new Error("Failed to save preferences: 500"),
    );
    const wrapper = open();
    await wrapper.find('[data-style="long"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".offer-error").text()).toContain("500");
    expect(preferences.value.date_format_offered).toBe(false);
  });
});

describe("PartialDateInput", () => {
  const field = (date = "", precision: "year" | "month" | null = null) =>
    mount(PartialDateInput, { props: { date, precision } });

  it("shows the expected order as its placeholder", () => {
    expect(field().find("input").attributes("placeholder")).toBe(
      "dd/mm/yyyy, mm/yyyy or yyyy",
    );
    preferences.value = { ...preferences.value, date_format: "mdy" };
    expect(field().find("input").attributes("placeholder")).toBe(
      "mm/dd/yyyy, mm/yyyy or yyyy",
    );
  });

  it("shows a stored partial date in the person's format without asking", () => {
    const wrapper = field("2011-04-01", "month");
    expect((wrapper.find("input").element as HTMLInputElement).value).toBe(
      "April 2011",
    );
    expect(wrapper.find("fieldset").exists()).toBe(false);
  });

  it("echoes how a typed date was read and emits ISO with its precision", async () => {
    const wrapper = field();
    await wrapper.find("input").setValue("2011");
    expect(wrapper.find(".partial-date-hint").text()).toBe("Read as 2011");
    expect(wrapper.emitted("update:date")!.at(-1)).toEqual(["2011-01-01"]);
    expect(wrapper.emitted("update:precision")!.at(-1)).toEqual(["year"]);
    await wrapper.find("input").setValue("Sep 2020");
    expect(wrapper.emitted("update:date")!.at(-1)).toEqual(["2020-09-01"]);
    expect(wrapper.emitted("update:precision")!.at(-1)).toEqual(["month"]);
  });

  it("asks which date was meant, and is not valid until one is picked", async () => {
    const wrapper = field();
    await wrapper.find("input").setValue("03/04/2011");
    expect(wrapper.emitted("update:valid")!.at(-1)).toEqual([false]);
    const choices = wrapper.findAll('input[type="radio"]');
    expect(wrapper.findAll("fieldset label").map((l) => l.text())).toEqual([
      "3 April 2011",
      "4 March 2011",
    ]);
    await choices[1].setValue(true);
    expect(wrapper.emitted("update:valid")!.at(-1)).toEqual([true]);
    expect(wrapper.emitted("update:date")!.at(-1)).toEqual(["2011-03-04"]);
    expect(wrapper.emitted("update:precision")!.at(-1)).toEqual([null]);
  });

  it("reads month first without asking once mm/dd/yyyy was chosen", async () => {
    preferences.value = {
      ...preferences.value,
      date_format: "mdy",
      date_format_chosen: true,
    };
    const wrapper = field();
    await wrapper.find("input").setValue("03/04/2011");
    expect(wrapper.find("fieldset").exists()).toBe(false);
    expect(wrapper.find(".partial-date-hint").text()).toBe(
      "Read as 4 March 2011",
    );
  });

  it("explains input it cannot read", async () => {
    const wrapper = field();
    await wrapper.find("input").setValue("31/02/2011");
    expect(wrapper.find("input").attributes("aria-invalid")).toBe("true");
    expect(wrapper.emitted("update:valid")!.at(-1)).toEqual([false]);
  });
});
