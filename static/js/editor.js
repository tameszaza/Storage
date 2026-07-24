(function () {
    "use strict";

    function plural(count, word) {
        return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("textEditorForm");
        const editor = document.getElementById("fileContentEditor");
        const lineNumbers = document.getElementById("editorLineNumbers");
        if (!form || !editor || !lineNumbers) return;

        const saveState = document.getElementById("editorSaveState");
        const saveButton = document.getElementById("saveEditorButton");
        const doneButton = document.getElementById("doneEditorButton");
        const position = document.getElementById("editorPosition");
        const lineCount = document.getElementById("editorLineCount");
        const wordCount = document.getElementById("editorWordCount");
        const characterCount = document.getElementById("editorCharacterCount");
        const wrapButton = document.getElementById("toggleWrapButton");
        const restoreButton = document.getElementById("restoreDraftButton");
        const discardButton = document.getElementById("discardDraftButton");
        const conflictBanner = document.getElementById("editorConflictBanner");
        const reloadButton = document.getElementById("reloadServerVersionButton");
        const mtimeInput = document.getElementById("editorMtimeInput");
        const revisionInput = document.getElementById("editorRevisionInput");
        const draftKey = form.dataset.draftKey;
        const folderUrl = form.dataset.folderUrl;

        let savedContent = editor.value;
        let mtimeNs = Number(form.dataset.mtimeNs) || null;
        let revision = String(form.dataset.revision || "") || null;
        let dirty = false;
        let saving = false;
        let draftTimer = null;

        function readDraftStorage() {
            if (!draftKey) return null;
            try {
                return localStorage.getItem(draftKey);
            } catch (_error) {
                return null;
            }
        }

        function writeDraftStorage(value) {
            if (!draftKey) return false;
            try {
                localStorage.setItem(draftKey, value);
                return true;
            } catch (_error) {
                return false;
            }
        }

        function clearDraftStorage() {
            if (!draftKey) return;
            try {
                clearDraftStorage();
            } catch (_error) {
                // Saving should not fail when browser storage is restricted.
            }
        }

        function setSaveState(label, icon, stateClass) {
            if (!saveState) return;
            saveState.className = `editor-save-state ${stateClass || ""}`.trim();
            saveState.innerHTML = `<i class="fa-solid ${icon}" aria-hidden="true"></i> ${label}`;
        }

        function updateButtons() {
            if (saveButton) saveButton.disabled = saving || !dirty;
            if (doneButton) doneButton.disabled = saving;
        }

        function updateLineNumbers() {
            const count = Math.max(1, editor.value.split("\n").length);
            lineNumbers.textContent = Array.from({ length: count }, (_, index) => index + 1).join("\n");
            if (lineCount) lineCount.textContent = plural(count, "line");
        }

        function updateCounts() {
            const text = editor.value;
            const words = text.trim() ? text.trim().split(/\s+/).length : 0;
            if (wordCount) wordCount.textContent = plural(words, "word");
            if (characterCount) characterCount.textContent = plural(text.length, "character");
        }

        function updatePosition() {
            const beforeCaret = editor.value.slice(0, editor.selectionStart);
            const lines = beforeCaret.split("\n");
            if (position) position.textContent = `Ln ${lines.length}, Col ${lines[lines.length - 1].length + 1}`;
        }

        function syncScroll() {
            lineNumbers.scrollTop = editor.scrollTop;
        }

        function saveDraft() {
            if (!draftKey || !dirty) return;
            const written = writeDraftStorage(JSON.stringify({ content: editor.value, savedAt: Date.now() }));
            setSaveState(
                written ? "Draft backed up" : "Draft backup unavailable",
                written ? "fa-cloud" : "fa-triangle-exclamation",
                written ? "local-draft" : "draft-error"
            );
        }

        function renderDirtyState() {
            dirty = editor.value !== savedContent;
            if (!saving) {
                setSaveState(dirty ? "Unsaved changes" : "Saved", dirty ? "fa-circle" : "fa-circle-check", dirty ? "unsaved" : "");
            }
            updateButtons();
        }

        function markChanged() {
            if (conflictBanner) conflictBanner.hidden = true;
            renderDirtyState();
            updateLineNumbers();
            updateCounts();
            updatePosition();
            window.clearTimeout(draftTimer);
            draftTimer = window.setTimeout(saveDraft, 500);
        }

        function inspectDraft() {
            if (!draftKey) return;
            try {
                const raw = readDraftStorage();
                if (!raw) return;
                const draft = JSON.parse(raw);
                if (draft.content !== savedContent) {
                    restoreButton.hidden = false;
                    discardButton.hidden = false;
                    restoreButton.title = `Restore draft saved ${new Date(draft.savedAt).toLocaleString()}`;
                } else {
                    clearDraftStorage();
                }
            } catch (error) {
                clearDraftStorage();
            }
        }

        async function saveToServer() {
            if (saving || !dirty) return true;
            let preserveResultState = false;
            saving = true;
            updateButtons();
            setSaveState("Saving", "fa-spinner fa-spin", "saving");
            if (conflictBanner) conflictBanner.hidden = true;

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    body: JSON.stringify({
                        file_content: editor.value,
                        expected_revision: revision,
                        expected_mtime_ns: mtimeNs,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (response.status === 409) {
                    if (conflictBanner) conflictBanner.hidden = false;
                    setSaveState("Conflict: reload before saving", "fa-triangle-exclamation", "draft-error");
                    preserveResultState = true;
                    return false;
                }
                if (!response.ok || !data.success) {
                    throw new Error(data.message || "Could not save the file.");
                }

                savedContent = editor.value;
                revision = String(data.revision || revision || "") || null;
                mtimeNs = Number(data.mtime_ns) || mtimeNs;
                form.dataset.revision = revision || "";
                form.dataset.mtimeNs = String(mtimeNs || "");
                if (revisionInput) revisionInput.value = revision || "";
                if (mtimeInput) mtimeInput.value = String(mtimeNs || "");
                dirty = false;
                clearDraftStorage();
                restoreButton.hidden = true;
                discardButton.hidden = true;
                setSaveState("Saved", "fa-circle-check", "");
                return true;
            } catch (error) {
                setSaveState(error.message || "Save failed", "fa-triangle-exclamation", "draft-error");
                preserveResultState = true;
                return false;
            } finally {
                saving = false;
                if (preserveResultState) updateButtons();
                else renderDirtyState();
            }
        }

        editor.addEventListener("input", markChanged);
        editor.addEventListener("scroll", syncScroll);
        editor.addEventListener("click", updatePosition);
        editor.addEventListener("keyup", updatePosition);
        editor.addEventListener("keydown", (event) => {
            if (event.key === "Tab") {
                event.preventDefault();
                editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end");
                markChanged();
            }
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
                event.preventDefault();
                saveToServer();
            }
        });

        form.addEventListener("submit", (event) => {
            event.preventDefault();
            saveToServer();
        });
        doneButton?.addEventListener("click", async () => {
            const saved = await saveToServer();
            if (saved) window.location.assign(folderUrl);
        });

        function setLineWrap(wrapped) {
            editor.classList.toggle("wrap-lines", wrapped);
            wrapButton?.setAttribute("aria-pressed", String(wrapped));
            wrapButton?.classList.toggle("active", wrapped);
        }

        wrapButton?.addEventListener("click", () => {
            setLineWrap(!editor.classList.contains("wrap-lines"));
        });

        restoreButton?.addEventListener("click", () => {
            try {
                const rawDraft = readDraftStorage();
                if (!rawDraft) throw new Error("No draft available.");
                const draft = JSON.parse(rawDraft);
                editor.value = draft.content;
                restoreButton.hidden = true;
                discardButton.hidden = false;
                markChanged();
                editor.focus();
            } catch (error) {
                restoreButton.hidden = true;
            }
        });

        discardButton?.addEventListener("click", () => {
            clearDraftStorage();
            restoreButton.hidden = true;
            discardButton.hidden = true;
            if (editor.value === savedContent) setSaveState("Saved", "fa-circle-check", "");
        });

        reloadButton?.addEventListener("click", () => window.location.reload());

        window.addEventListener("beforeunload", (event) => {
            if (!dirty) return;
            event.preventDefault();
            event.returnValue = "";
        });

        setLineWrap(window.matchMedia("(max-width: 600px)").matches);
        updateLineNumbers();
        updateCounts();
        updatePosition();
        inspectDraft();
        renderDirtyState();
    });
})();
