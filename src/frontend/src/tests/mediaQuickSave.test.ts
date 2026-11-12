// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { expect, it, vi } from "vitest";
import { useMediaQuickSave } from "../composables/useMediaQuickSave";
import MyNote from "../components/MyNote.vue";

interface Row {
  id: string;
  favorite: boolean;
  note: string | null;
  rating: number | null;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const row = (extra: Partial<Row> = {}): Row => ({
  id: "a",
  favorite: false,
  note: null,
  rating: null,
  ...extra,
});

it("sends one save at a time and undoes only the change that failed", async () => {
  const record = ref<Row | null>(row());
  const first = deferred<Row>();
  const bodies: Row[] = [];
  const persist = vi.fn(async (_id: string, changes: Partial<Row>) => {
    bodies.push(changes as Row);
    if (bodies.length === 1) return first.promise;
    throw new Error("Down");
  });
  const { quickSave, saveError } = useMediaQuickSave(record, persist);
  const favourite = quickSave((r) => (r.favorite = true), "fav failed");
  const rating = quickSave((r) => (r.rating = 4), "rating failed");
  expect(record.value).toMatchObject({ favorite: true, rating: 4 });
  await flushPromises();
  expect(persist).toHaveBeenCalledTimes(1);
  first.resolve(row({ favorite: true }));
  expect(await favourite).toBe(true);
  expect(await rating).toBe(false);
  // The second save was the accepted record plus its own change only.
  expect(bodies[1]).toEqual({ rating: 4 });
  expect(record.value).toMatchObject({ favorite: true, rating: null });
  expect(saveError.value).toBe("rating failed");
});

it("keeps a late answer off a title the page has left", async () => {
  const record = ref<Row | null>(row());
  const slow = deferred<Row>();
  const { quickSave, saveError } = useMediaQuickSave(
    record,
    () => slow.promise,
  );
  const pending = quickSave((r) => (r.favorite = true), "failed");
  record.value = row({ id: "b" });
  slow.reject(new Error("Down"));
  await pending;
  expect(record.value).toEqual(row({ id: "b" }));
  expect(saveError.value).toBeNull();
});

it("keeps the note editor and its text open when the save fails", async () => {
  let accept = false;
  const saveNote = vi.fn(async () => accept);
  const wrapper = mount(MyNote, { props: { note: null, saveNote } });
  await wrapper.find("button").trigger("click"); // Add a note
  await wrapper.find("textarea").setValue("Remember the ending");
  await wrapper.find(".btn-solid").trigger("click");
  await flushPromises();
  expect(saveNote).toHaveBeenCalledWith("Remember the ending");
  expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe(
    "Remember the ending",
  );
  expect(wrapper.find("[role=alert]").text()).toContain("could not be saved");
  accept = true;
  await wrapper.find(".btn-solid").trigger("click");
  await flushPromises();
  expect(wrapper.find("textarea").exists()).toBe(false);
});

it("retains a newer note typed while an earlier save succeeds", async () => {
  const slow = deferred<boolean>();
  const wrapper = mount(MyNote, {
    props: { note: null, saveNote: () => slow.promise },
  });
  await wrapper.find("button").trigger("click");
  await wrapper.find("textarea").setValue("Submitted text");
  await wrapper.find(".btn-solid").trigger("click");
  await wrapper.find("textarea").setValue("Newer unsaved text");
  slow.resolve(true);
  await flushPromises();
  expect(wrapper.find("textarea").exists()).toBe(true);
  expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe(
    "Newer unsaved text",
  );
  wrapper.unmount();
});

it("keeps an independent confirmed rename out of later quick patches", async () => {
  const record = ref<(Row & { title: string }) | null>({
    ...row(),
    title: "Original",
  });
  const slow = deferred<Row & { title: string }>();
  const persist = vi
    .fn()
    .mockReturnValueOnce(slow.promise)
    .mockResolvedValueOnce({
      ...row({ favorite: true, rating: 7 }),
      title: "Renamed",
    });
  const { quickSave } = useMediaQuickSave(record, persist);
  const favorite = quickSave((r) => (r.favorite = true), "failed");
  await flushPromises();
  record.value = { ...row({ favorite: true }), title: "Renamed" };
  const rating = quickSave((r) => (r.rating = 7), "failed");
  slow.resolve({ ...row({ favorite: true }), title: "Original" });
  await Promise.all([favorite, rating]);
  expect(persist.mock.calls.map(([id, patch]) => [id, patch])).toEqual([
    ["a", { favorite: true }],
    ["a", { rating: 7 }],
  ]);
  expect(record.value).toMatchObject({
    title: "Renamed",
    favorite: true,
    rating: 7,
  });
});

// Review of 2447a586 (olie-ob): an editor response that replaces the record but
// leaves this field as the server last confirmed it is not an update to the
// field, so it must neither discard this field's result nor hide its failure.
it("applies a quick save that succeeds after an unrelated editor response", async () => {
  const record = ref<(Row & { title: string }) | null>({
    ...row(),
    title: "Original",
  });
  const slow = deferred<Row & { title: string }>();
  const { quickSave, saveError } = useMediaQuickSave(
    record,
    () => slow.promise,
  );
  const favorite = quickSave((r) => (r.favorite = true), "fav failed");
  await flushPromises();
  // The full editor saved a rename; its response still has the old favorite.
  record.value = { ...row({ favorite: false }), title: "Renamed" };
  // The pending change keeps showing until its own answer arrives.
  expect(record.value).toMatchObject({ title: "Renamed", favorite: true });
  slow.resolve({ ...row({ favorite: true }), title: "Renamed" });
  expect(await favorite).toBe(true);
  expect(record.value).toMatchObject({ title: "Renamed", favorite: true });
  expect(saveError.value).toBeNull();
});

it("reports a quick save that fails after an unrelated editor response", async () => {
  const record = ref<(Row & { title: string }) | null>({
    ...row(),
    title: "Original",
  });
  const slow = deferred<Row & { title: string }>();
  const { quickSave, saveError } = useMediaQuickSave(
    record,
    () => slow.promise,
  );
  const favorite = quickSave((r) => (r.favorite = true), "fav failed");
  await flushPromises();
  record.value = { ...row({ favorite: false }), title: "Renamed" };
  slow.reject(new Error("Down"));
  expect(await favorite).toBe(false);
  // The rename stays, the failed favorite is undone and reported.
  expect(record.value).toMatchObject({ title: "Renamed", favorite: false });
  expect(saveError.value).toBe("fav failed");
});

it("lets an editor's own change to the field win over an older quick save", async () => {
  const record = ref<Row | null>(row());
  const slow = deferred<Row>();
  const { quickSave, saveError } = useMediaQuickSave(
    record,
    () => slow.promise,
  );
  const rating = quickSave((r) => (r.rating = 4), "rating failed");
  await flushPromises();
  // The editor saved a different rating after the quick save was sent.
  record.value = row({ rating: 9 });
  expect(record.value.rating).toBe(9);
  slow.reject(new Error("Down"));
  await rating;
  expect(record.value).toMatchObject({ rating: 9 });
  expect(saveError.value).toBeNull();
});

it("clears an older status failure once a newer save of it succeeds", async () => {
  const record = ref<Row | null>(row());
  const first = deferred<Row>();
  const persist = vi
    .fn()
    .mockReturnValueOnce(first.promise)
    .mockResolvedValueOnce(row({ rating: 2 }));
  const { quickSave, saveError } = useMediaQuickSave(record, persist);
  const playing = quickSave((r) => (r.rating = 1), "status failed");
  const beaten = quickSave((r) => (r.rating = 2), "status failed");
  first.reject(new Error("Down"));
  await Promise.all([playing, beaten]);
  expect(record.value).toMatchObject({ rating: 2 });
  expect(saveError.value).toBeNull();
});
