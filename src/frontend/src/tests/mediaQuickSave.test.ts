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
  const persist = vi.fn(async (_id: string, next: Row) => {
    bodies.push(next);
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
  // The second save sent the server's accepted record plus its own change.
  expect(bodies[1]).toEqual(row({ favorite: true, rating: 4 }));
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

// olie-ob's review of #536: reopening and saving an unchanged note must be a no-op success,
// through a saveNote wired to quickSave exactly as the movie/TV/anime pages wire it.
it("reopening and saving an unchanged note reports success, not a failure", async () => {
  const record = ref<Row | null>(row({ note: "Already here" }));
  const persist = vi.fn();
  const { quickSave } = useMediaQuickSave(record, persist);
  const saveNote = (note: string | null) =>
    quickSave((r) => (r.note = note), "Could not save your note. Try again.");
  const wrapper = mount(MyNote, {
    props: { note: "Already here", saveNote },
  });
  await wrapper.find("button").trigger("click"); // Edit
  await wrapper.find(".btn-solid").trigger("click"); // Save, unchanged
  await flushPromises();
  expect(persist).not.toHaveBeenCalled();
  expect(wrapper.find("[role=alert]").exists()).toBe(false);
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
  expect(persist.mock.calls).toEqual([
    ["a", { ...row({ favorite: true }), title: "Original" }],
    ["a", { ...row({ favorite: true, rating: 7 }), title: "Renamed" }],
  ]);
  expect(record.value).toMatchObject({
    title: "Renamed",
    favorite: true,
    rating: 7,
  });
});

// olie-ob's review of #536: saving a value that already matches has no changed fields, so
// `sending.length === 0` returned `saved === false` - a no-op was reported as a failure.
it("reports a quick save that changes nothing as saved, not failed", async () => {
  const record = ref<Row | null>(row({ note: "Same" }));
  const persist = vi.fn();
  const { quickSave, saveError } = useMediaQuickSave(record, persist);
  const result = await quickSave((r) => (r.note = "Same"), "note failed");
  expect(result).toBe(true);
  expect(persist).not.toHaveBeenCalled();
  expect(saveError.value).toBeNull();
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

// The movie/TV/anime detail endpoints only take a full update (there is no
// partial PATCH), so a queued quick save must re-check which of its fields
// it still owns right before it builds that full body — otherwise it would
// ship a value the full editor has since replaced.
it("does not send a queued quick save of a field the editor has since replaced", async () => {
  const record = ref<Row | null>(row());
  const first = deferred<Row>();
  const sent: Row[] = [];
  const persist = vi.fn(async (_id: string, next: Row) => {
    sent.push(next);
    if (sent.length === 1) return first.promise;
    return next;
  });
  const { quickSave, saveError } = useMediaQuickSave(record, persist);
  const favorite = quickSave((r) => (r.favorite = true), "fav failed");
  const rating = quickSave((r) => (r.rating = 3), "rating failed");
  await flushPromises();
  // The full editor saves rating 5 (a separate, already-settled request)
  // while the rating quick save is still queued behind the favourite one.
  record.value = { ...row({ rating: 5 }), favorite: true };
  first.resolve(row({ favorite: true }));
  expect(await favorite).toBe(true);
  expect(await rating).toBe(false);
  expect(sent).toHaveLength(1);
  expect(record.value).toMatchObject({ favorite: true, rating: 5 });
  expect(saveError.value).toBeNull();
});

it("sends only the fields of a queued quick save the editor left alone", async () => {
  type Wide = Row & { title: string };
  const record = ref<Wide | null>({ ...row(), title: "Original" });
  const first = deferred<Wide>();
  const sent: Wide[] = [];
  const persist = vi.fn(async (_id: string, next: Wide) => {
    sent.push(next);
    if (sent.length === 1) return first.promise;
    return next;
  });
  const { quickSave } = useMediaQuickSave(record, persist);
  const favorite = quickSave((r) => (r.favorite = true), "fav failed");
  const both = quickSave((r) => {
    r.rating = 3;
    r.note = "quick";
  }, "both failed");
  await flushPromises();
  // The editor response carries a different rating; the queued save's own
  // (now-stale) rating must not be the one that goes out in its full body.
  record.value = { ...row({ favorite: true, rating: 5 }), title: "Original" };
  first.resolve({ ...row({ favorite: true }), title: "Original" });
  await favorite;
  expect(await both).toBe(true);
  expect(sent[1]).toMatchObject({ rating: 5, note: "quick" });
  expect(record.value).toMatchObject({ rating: 5, note: "quick" });
});

it("skips and does not report a quick save superseded before its request left", async () => {
  const record = ref<Row | null>(row());
  const persist = vi.fn(() => Promise.reject(new Error("Down")));
  const { quickSave, saveError } = useMediaQuickSave(record, persist);
  const rating = quickSave((r) => (r.rating = 3), "rating failed");
  record.value = row({ rating: 5 });
  expect(await rating).toBe(false);
  expect(persist).not.toHaveBeenCalled();
  expect(record.value).toMatchObject({ rating: 5 });
  expect(saveError.value).toBeNull();
});

it("does not report a superseded failure while the request is in flight", async () => {
  const record = ref<Row | null>(row());
  const slow = deferred<Row>();
  const { quickSave, saveError } = useMediaQuickSave(
    record,
    () => slow.promise,
  );
  const note = quickSave((r) => (r.note = "x"), "note failed");
  await flushPromises();
  record.value = row({ note: "editor" });
  slow.reject(new Error("Down"));
  expect(await note).toBe(false);
  expect(record.value).toMatchObject({ note: "editor" });
  expect(saveError.value).toBeNull();
});

// olie-ob's review of #536: useMediaQuickSave only serialized quick saves with one another;
// the full editor and progress saves issued their own independent full-record writes, so one
// delayed quick save could still commit last and overwrite an edit it never knew about.
it("serializes an editor save behind an in-flight quick save, so neither overwrites the other", async () => {
  type Wide = Row & { title: string };
  const record = ref<Wide | null>({ ...row(), title: "Old" });
  const favSlow = deferred<Wide>();
  const persist = vi.fn(() => favSlow.promise);
  const { quickSave, reconcile } = useMediaQuickSave(record, persist);

  const favorite = quickSave((r) => (r.favorite = true), "fav failed");
  await flushPromises();
  expect(persist).toHaveBeenCalledTimes(1);

  // The editor starts saving a rename while the favourite save is still in flight.
  const editorWrite = vi.fn(async (confirmed: Wide) => ({
    ...confirmed,
    title: "New",
  }));
  const editorSave = reconcile("a", editorWrite);
  await Promise.resolve();
  await Promise.resolve();
  // It must not run - let alone commit - until the quick save ahead of it settles.
  expect(editorWrite).not.toHaveBeenCalled();

  favSlow.resolve({ ...row({ favorite: true }), title: "Old" });
  expect(await favorite).toBe(true);
  const saved = await editorSave;

  // The editor's write was built from the quick save's own confirmed result, never a stale
  // read from before the favourite save settled, and nothing is lost either way.
  expect(editorWrite).toHaveBeenCalledWith(
    expect.objectContaining({ favorite: true, title: "Old" }),
  );
  expect(saved).toMatchObject({ favorite: true, title: "New" });
  expect(record.value).toMatchObject({ favorite: true, title: "New" });
});

it("serializes a quick save behind an in-flight editor save, using its result as the base", async () => {
  type Wide = Row & { title: string };
  const record = ref<Wide | null>({ ...row(), title: "Old" });
  const editorSlow = deferred<Wide>();
  const persist = vi.fn(async (_id: string, next: Wide) => next);
  const { quickSave, reconcile } = useMediaQuickSave(record, persist);

  const editorSave = reconcile("a", () => editorSlow.promise);
  const favorite = quickSave((r) => (r.favorite = true), "fav failed");
  await flushPromises();
  // The quick save's request must not go out until the editor's save ahead of it settles.
  expect(persist).not.toHaveBeenCalled();

  editorSlow.resolve({ ...row(), title: "New" });
  await editorSave;
  expect(await favorite).toBe(true);

  expect(persist).toHaveBeenCalledWith(
    "a",
    expect.objectContaining({ favorite: true, title: "New" }),
  );
  expect(record.value).toMatchObject({ favorite: true, title: "New" });
});

it("keeps the newest draft when serialized answers resolve out of order", async () => {
  const record = ref<Row | null>(row());
  const p1 = deferred<Row>();
  const p2 = deferred<Row>();
  let calls = 0;
  const { quickSave } = useMediaQuickSave(record, () =>
    ++calls === 1 ? p1.promise : p2.promise,
  );
  const s1 = quickSave((r) => (r.rating = 1), "e");
  const s2 = quickSave((r) => (r.rating = 2), "e");
  expect(record.value!.rating).toBe(2);
  p2.resolve(row({ rating: 2 }));
  p1.resolve(row({ rating: 1 }));
  await s1;
  expect(record.value!.rating).toBe(2);
  await s2;
  expect(record.value!.rating).toBe(2);
});
