/* File > Import: drop a radar line to open it, or drawings to keep beside the data.
 *
 * The server decides what is acceptable (studio/imports.py) and says why when it
 * isn't; this dialog shows that reason verbatim rather than guessing it locally.
 */

import * as api from "./api.js";
import * as documents from "./documents.js";
import * as modal from "./modal.js";
import { el, replace } from "./dom.js";

const LINE_EXTENSIONS = [".rad", ".ra1", ".ra2", ".gps", ".map"];

function isLineFile(file) {
  const name = file.name.toLowerCase();
  return LINE_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/** A drop zone that also opens the file picker on click. Calls onFiles(File[]). */
function dropZone(text, accept, onFiles) {
  const input = el("input", { type: "file", multiple: true, accept, hidden: true,
    onchange: () => { onFiles([...input.files]); input.value = ""; } });
  const zone = el("div", { class: "drop-zone", tabindex: 0, role: "button",
    onclick: () => input.click(),
    onkeydown: (event) => { if (event.key === "Enter" || event.key === " ") input.click(); },
    ondragover: (event) => { event.preventDefault(); zone.classList.add("over"); },
    ondragleave: () => zone.classList.remove("over"),
    ondrop: (event) => {
      event.preventDefault();
      zone.classList.remove("over");
      onFiles([...event.dataTransfer.files]);
    },
  }, [el("span", { text }), input]);
  return zone;
}

function referenceList(items) {
  if (!items.length) return el("p", { class: "empty", text: "No drawings imported yet." });
  return el("ul", { class: "reference-list" }, items.map((item) =>
    el("li", {}, [
      el("a", { href: api.importedReferenceUrl(item.name), target: "_blank", rel: "noopener", text: item.name }),
      el("span", { class: "mono", text: item.size_bytes < 1e6 ? `${Math.max(1, Math.round(item.size_bytes / 1e3))} KB` : `${(item.size_bytes / 1e6).toFixed(1)} MB` }),
    ])));
}

/** Open the dialog. `onLineImported(job)` runs after a line lands, so the caller can open it. */
export async function open({ onLineImported }) {
  let pending = [];
  const status = el("p", { class: "import-status" });
  const fileList = el("p", { class: "mono import-files", text: "No files chosen." });
  const jobInput = el("input", { type: "text", placeholder: "e.g. Job_0801", style: "width:100%" });
  const refs = el("div", {}, [el("p", { class: "empty", text: "Loading…" })]);

  const say = (text, isError = false) => {
    status.textContent = text;
    status.classList.toggle("error", isError);
  };

  const refreshRefs = async () => replace(refs, referenceList(await api.listImportedReferences()));

  const chooseLineFiles = (files) => {
    pending = files;
    fileList.textContent = files.length ? files.map((f) => f.name).join(", ") : "No files chosen.";
    const stray = files.filter((f) => !isLineFile(f));
    say(stray.length
      ? `${stray.map((f) => f.name).join(", ")} ${stray.length === 1 ? "is" : "are"} not part of a radar line — drop drawings on the Reference drawings side instead.`
      : "", stray.length > 0);
  };

  const importLine = async () => {
    const job = jobInput.value.trim();
    if (!job) return say("Give the line a job name first.", true);
    if (!pending.length) return say("Choose the line's .RAD / .RA1 / .RA2 files first.", true);
    say("Importing and checking every channel…");
    try {
      await api.importLine(job, pending);
      modal.close();
      onLineImported(job);
    } catch (error) {
      say(error.message, true);
    }
  };

  const importDrawings = async (files) => {
    if (!files.length) return;
    say(`Uploading ${files.length} drawing${files.length === 1 ? "" : "s"}…`);
    try {
      const { files: stored } = await api.importReferences(files);
      say(`Stored ${stored.join(", ")}.`);
      await refreshRefs();
      documents.render();
    } catch (error) {
      say(error.message, true);
    }
  };

  modal.open(
    el("h3", { text: "Import files" }),
    el("div", { class: "import-grid" }, [
      el("section", {}, [
        el("h4", { text: "Radar line" }),
        el("p", { text: "The line's .RAD, .RA1 and .RA2 channels, plus its .gps and .map if you have them. It opens as a new job once every channel reads correctly." }),
        el("label", { class: "import-label", text: "Job name" }),
        jobInput,
        dropZone("Drop line files here, or click to choose", LINE_EXTENSIONS.join(","), chooseLineFiles),
        fileList,
        el("div", { class: "btn-row", style: "padding:6px 0 0" }, [
          el("button", { class: "btn primary", text: "Import line", onclick: importLine }),
        ]),
      ]),
      el("section", {}, [
        el("h4", { text: "Reference drawings" }),
        el("p", { text: "Deliverable sheets, CAD drawings, reports and radargram screenshots (PDF, DWG, DXF, PNG, JPG, ZIP, RAR). Kept alongside the data for reference — not processed." }),
        dropZone("Drop drawings here, or click to choose", ".pdf,.dwg,.dxf,.png,.jpg,.jpeg,.zip,.rar", importDrawings),
        refs,
      ]),
    ]),
    status,
    el("div", { class: "btn-row", style: "padding:8px 0 0" }, [
      el("button", { class: "btn ghost", text: "Close", onclick: () => modal.close() }),
    ]),
  );
  jobInput.focus();
  refreshRefs().catch((error) => replace(refs, el("p", { class: "empty", text: error.message })));
}

/** File > Reference drawings: just the list, with working links. */
export async function showReferences() {
  const body = el("div", {}, [el("p", { class: "empty", text: "Loading…" })]);
  modal.open(
    el("h3", { text: "Reference drawings" }),
    body,
    el("div", { class: "btn-row", style: "padding:8px 0 0" }, [
      el("button", { class: "btn ghost", text: "Close", onclick: () => modal.close() }),
    ]),
  );
  try {
    replace(body, referenceList(await api.listImportedReferences()));
  } catch (error) {
    replace(body, el("p", { class: "empty", text: error.message }));
  }
}
