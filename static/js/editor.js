(function () {
    function plural(count, word) {
        return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("textEditorForm");
        const editor = document.getElementById("fileContentEditor");
        const lineNumbers = document.getElementById("editorLineNumbers");
        if (!form || !editor || !lineNumbers) return;

        const saveState = document.getElementById("editorSaveState");
        const position = document.getElementById("editorPosition");
        const lineCount = document.getElementById("editorLineCount");
        const wordCount = document.getElementById("editorWordCount");
        const characterCount = document.getElementById("editorCharacterCount");
        const wrapButton = document.getElementById("toggleWrapButton");
        const restoreButton = document.getElementById("restoreDraftButton");
        const discardButton = document.getElementById("discardDraftButton");
        const draftKey = form.dataset.draftKey;
        const initialContent = editor.value;
        let dirty = false;
        let draftTimer = null;

        function setSaveState(label, icon, stateClass) {
            if (!saveState) return;
            saveState.className = `editor-save-state ${stateClass || ""}`.trim();
            saveState.innerHTML = `<i class="fa-solid ${icon}" aria-hidden="true"></i> ${label}`;
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
            try {
                localStorage.setItem(draftKey, JSON.stringify({ content: editor.value, savedAt: Date.now() }));
                setSaveState("Draft saved locally", "fa-cloud", "local-draft");
            } catch (error) {
                setSaveState("Local draft unavailable", "fa-triangle-exclamation", "draft-error");
            }
        }

        function markChanged() {
            dirty = editor.value !== initialContent;
            setSaveState(dirty ? "Unsaved changes" : "Saved on server", dirty ? "fa-circle" : "fa-circle-check", dirty ? "unsaved" : "");
            updateLineNumbers();
            updateCounts();
            updatePosition();
            window.clearTimeout(draftTimer);
            draftTimer = window.setTimeout(saveDraft, 500);
        }

        function inspectDraft() {
            if (!draftKey) return;
            try {
                const raw = localStorage.getItem(draftKey);
                if (!raw) return;
                const draft = JSON.parse(raw);
                if (draft.content !== initialContent) {
                    restoreButton.hidden = false;
                    discardButton.hidden = false;
                    const time = new Date(draft.savedAt).toLocaleString();
                    restoreButton.title = `Restore browser draft saved ${time}`;
                } else {
                    localStorage.removeItem(draftKey);
                }
            } catch (error) {
                localStorage.removeItem(draftKey);
            }
        }

        editor.addEventListener("input", markChanged);
        editor.addEventListener("scroll", syncScroll);
        editor.addEventListener("click", updatePosition);
        editor.addEventListener("keyup", updatePosition);
        editor.addEventListener("keydown", (event) => {
            if (event.key === "Tab") {
                event.preventDefault();
                const start = editor.selectionStart;
                const end = editor.selectionEnd;
                editor.setRangeText("    ", start, end, "end");
                markChanged();
            }
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
                event.preventDefault();
                form.requestSubmit(form.querySelector('button[name="save_action"][value="continue"]'));
            }
        });

        wrapButton?.addEventListener("click", () => {
            const wrapped = editor.classList.toggle("wrap-lines");
            wrapButton.setAttribute("aria-pressed", String(wrapped));
            wrapButton.classList.toggle("active", wrapped);
        });

        restoreButton?.addEventListener("click", () => {
            try {
                const draft = JSON.parse(localStorage.getItem(draftKey));
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
            localStorage.removeItem(draftKey);
            restoreButton.hidden = true;
            discardButton.hidden = true;
            if (editor.value === initialContent) setSaveState("Saved on server", "fa-circle-check", "");
        });

        form.addEventListener("submit", () => {
            dirty = false;
            if (draftKey) localStorage.removeItem(draftKey);
            setSaveState("Saving…", "fa-spinner fa-spin", "saving");
        });

        window.addEventListener("beforeunload", (event) => {
            if (!dirty) return;
            event.preventDefault();
            event.returnValue = "";
        });

        updateLineNumbers();
        updateCounts();
        updatePosition();
        inspectDraft();
    });
})();
