/* Left-dock "Survey documents": drawings, reports and images found in the dataset folder,
 * plus anything imported as a reference drawing. Grouped by folder, one click to open.
 *
 * Read-only by design — Studio cannot extract radar data from a drawing, so these are here to
 * be looked at beside the radargram, not processed.
 */

import * as api from "./api.js";
import { el, replace } from "./dom.js";

const tree = document.getElementById("docTree");
const count = document.getElementById("docCount");

function group(title, fullPath, docs, urlFor) {
  return el("details", { class: "doc-group" }, [
    el("summary", { title: fullPath || title }, [
      el("span", { class: "doc-group-name", text: title }),
      el("span", { class: "doc-group-count", text: String(docs.length) }),
    ]),
    ...docs.map((doc) =>
      el("a", {
        class: "doc-link",
        href: urlFor(doc),
        target: "_blank",
        rel: "noopener",
        title: `${doc.name} · ${(doc.size_bytes / 1e6).toFixed(1)} MB`,
        text: doc.name,
      })),
  ]);
}

export async function render() {
  let docs = [];
  let uploads = [];
  try {
    [docs, uploads] = await Promise.all([api.listDocuments(), api.listImportedReferences()]);
  } catch (error) {
    replace(tree, el("p", { class: "empty", text: error.message }));
    return;
  }
  const byFolder = new Map();
  for (const doc of docs) {
    if (!byFolder.has(doc.folder)) byFolder.set(doc.folder, []);
    byFolder.get(doc.folder).push(doc);
  }
  const groups = [...byFolder].map(([folder, items]) =>
    group(folder.split("/").pop() || "Dataset folder", folder, items, (d) => api.documentUrl(d.path)));
  if (uploads.length) {
    groups.push(group("Imported drawings", "", uploads, (d) => api.importedReferenceUrl(d.name)));
  }
  count.textContent = String(docs.length + uploads.length);
  replace(tree, ...(groups.length ? groups
    : [el("p", { class: "empty", text: "No drawings or reports in the dataset folder." })]));
}
