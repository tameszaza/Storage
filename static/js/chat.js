(function () {
    const STORAGE_KEY = "tamestorage.ai.chat.v3";
    const imageUrlsToRevoke = [];
    const state = {
        messages: [],
        imageFile: null,
        imageUrl: "",
    };

    function $(id) { return document.getElementById(id); }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function formatAssistantText(text) {
        let escaped = escapeHtml(text || "");
        escaped = escaped.replace(/```([\s\S]*?)```/g, function (_, code) {
            return "<pre><code>" + code.trim() + "</code></pre>";
        });
        return escaped
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
            .replace(/\n/g, "<br>");
    }

    function newId() {
        return crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random());
    }

    function saveHistory() {
        const serializable = state.messages.map((message) => ({
            id: message.id,
            role: message.role,
            text: message.text,
            imageName: message.imageName || "",
            actionUrl: message.actionUrl || "",
            actionLabel: message.actionLabel || "",
            createdAt: message.createdAt || Date.now(),
        }));
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(serializable));
        } catch (_error) {
            // Chat remains available when browser history storage is restricted.
        }
    }

    function loadHistory() {
        try {
            const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
            if (Array.isArray(parsed)) state.messages = parsed;
        } catch (error) {
            state.messages = [];
        }
        if (!state.messages.length) {
            state.messages = [{
                id: newId(),
                role: "assistant",
                text: "Hi. Ask me anything about your storage workspace. I can also run safe commands like `/open path`, `/move source -> folder`, `/copy source -> folder`, and `/rename source -> new-name`.",
                createdAt: Date.now(),
            }];
        }
    }

    function getPreferences() {
        return {
            includeContext: $("includeTreeContext")?.checked ? "1" : "0",
            filePath: $("fileContextPath")?.value.trim() || "",
            detailLevel: $("detailLevel")?.value || "balanced",
            responseStyle: $("responseStyle")?.value || "practical",
        };
    }

    function scrollToBottom() {
        const container = $("chatMessages");
        if (container) container.scrollTop = container.scrollHeight;
    }

    function messageById(id) {
        return state.messages.find((message) => message.id === id);
    }

    function previousUserMessage(messageId) {
        const index = state.messages.findIndex((message) => message.id === messageId);
        for (let i = index; i >= 0; i -= 1) {
            if (state.messages[i]?.role === "user" && state.messages[i]?.text) return state.messages[i];
        }
        return null;
    }

    function makeSmallButton(html, title, onClick) {
        const button = document.createElement("button");
        button.className = "message-tool-button";
        button.type = "button";
        button.innerHTML = html;
        if (title) button.title = title;
        button.addEventListener("click", onClick);
        return button;
    }

    function makeMessageActions(message) {
        const actions = document.createElement("div");
        actions.className = "message-actions inline-message-actions";

        actions.appendChild(makeSmallButton('<i class="fa-regular fa-copy"></i> Copy', "Copy message", async (event) => {
            const button = event.currentTarget;
            const text = message.text || "";
            try {
                await navigator.clipboard.writeText(text);
                button.innerHTML = '<i class="fa-solid fa-check"></i> Copied';
                setTimeout(() => { button.innerHTML = '<i class="fa-regular fa-copy"></i> Copy'; }, 1200);
            } catch (error) {
                window.prompt("Copy message", text);
            }
        }));

        actions.appendChild(makeSmallButton('<i class="fa-solid fa-rotate-right"></i> Regenerate', "Regenerate from this message", () => {
            regenerateFromMessage(message.id);
        }));

        if (message.role === "user") {
            actions.appendChild(makeSmallButton('<i class="fa-solid fa-pen"></i> Reuse', "Put this text back into the composer", () => {
                const input = $("chatInput");
                if (input) {
                    input.value = message.text || "";
                    input.focus();
                    autoResizeInput();
                }
            }));
        }

        if (message.actionUrl) {
            const link = document.createElement("a");
            link.className = "message-tool-button message-action-link";
            link.href = message.actionUrl;
            link.innerHTML = '<i class="fa-solid fa-arrow-up-right-from-square"></i> ' + escapeHtml(message.actionLabel || "Open");
            actions.appendChild(link);
        }

        return actions;
    }

    function renderMessages() {
        const container = $("chatMessages");
        if (!container) return;
        const query = ($("chatSearchInput")?.value || "").trim().toLowerCase();
        container.innerHTML = "";

        state.messages.forEach((message) => {
            const text = message.text || "";
            const isMatch = query && text.toLowerCase().includes(query);
            if (query && !isMatch) return;

            const wrapper = document.createElement("div");
            wrapper.className = "message " + message.role + (isMatch ? " search-hit" : "");
            wrapper.dataset.messageId = message.id;

            const avatar = document.createElement("span");
            avatar.className = "message-avatar";
            avatar.innerHTML = message.role === "user" ? '<i class="fa-solid fa-user"></i>' : '<i class="fa-solid fa-robot"></i>';

            const content = document.createElement("div");
            content.className = "message-content";

            const bubble = document.createElement("div");
            bubble.className = "message-bubble";

            if (message.imageUrl) {
                const imageWrap = document.createElement("div");
                imageWrap.className = "message-image-wrap";
                const image = document.createElement("img");
                image.className = "message-image";
                image.src = message.imageUrl;
                image.alt = message.imageName || "Attached image";
                imageWrap.appendChild(image);
                bubble.appendChild(imageWrap);
            } else if (message.imageName) {
                const imageNote = document.createElement("div");
                imageNote.className = "message-attachment-note";
                imageNote.innerHTML = '<i class="fa-solid fa-image"></i> ' + escapeHtml(message.imageName);
                bubble.appendChild(imageNote);
            }

            const body = document.createElement("div");
            body.className = "message-body";
            if (message.role === "assistant") body.innerHTML = formatAssistantText(text);
            else body.textContent = text || (message.imageName ? "Attached image" : "");
            bubble.appendChild(body);

            content.appendChild(bubble);
            content.appendChild(makeMessageActions(message));
            wrapper.appendChild(avatar);
            wrapper.appendChild(content);
            container.appendChild(wrapper);
        });
    }

    function addMessage(role, text, options = {}) {
        const message = {
            id: newId(),
            role,
            text: text || "",
            imageUrl: options.imageUrl || "",
            imageName: options.imageName || "",
            actionUrl: options.actionUrl || "",
            actionLabel: options.actionLabel || "",
            createdAt: Date.now(),
        };
        state.messages.push(message);
        saveHistory();
        renderMessages();
        scrollToBottom();
        return message;
    }

    function updateMessage(id, text, options = {}) {
        const message = messageById(id);
        if (!message) return;
        message.text = text || "";
        message.actionUrl = options.actionUrl || "";
        message.actionLabel = options.actionLabel || "";
        saveHistory();
        renderMessages();
        scrollToBottom();
    }

    function setBusy(isBusy) {
        const button = $("sendButton");
        if (!button) return;
        button.disabled = isBusy;
        button.innerHTML = isBusy ? '<i class="fa-solid fa-circle-notch fa-spin"></i> Sending' : '<i class="fa-solid fa-paper-plane"></i> Send';
    }

    function updateImageLabel() {
        const label = $("imageLabel");
        const removeButton = $("removeImageBtn");
        if (!label) return;
        label.textContent = state.imageFile ? state.imageFile.name : "Attach image";
        label.title = state.imageFile ? state.imageFile.name : "";
        if (removeButton) removeButton.hidden = !state.imageFile;
    }

    function setImageFile(file) {
        if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
        state.imageFile = file || null;
        state.imageUrl = file ? URL.createObjectURL(file) : "";
        if (state.imageUrl) imageUrlsToRevoke.push(state.imageUrl);
        updateImageLabel();
    }

    function clearImage() {
        const imageInput = $("imageInput");
        if (imageInput) imageInput.value = "";
        setImageFile(null);
    }

    async function sendPrompt(promptText, options = {}) {
        const message = String(promptText || "").trim();
        const imageFile = options.imageFile || null;
        if (!message && !imageFile) return;

        if (!options.skipUserBubble) {
            addMessage("user", message, {
                imageUrl: imageFile ? state.imageUrl : "",
                imageName: imageFile ? imageFile.name : "",
            });
        }

        const thinking = addMessage("assistant", "Thinking...");
        const formData = new FormData();
        formData.append("msg", message);
        formData.append("prompt", $("imagePromptInput")?.value || "Describe this image.");
        const prefs = getPreferences();
        formData.append("include_context", prefs.includeContext);
        formData.append("file_path", prefs.filePath);
        formData.append("detail_level", prefs.detailLevel);
        formData.append("response_style", prefs.responseStyle);
        if (imageFile) formData.append("image", imageFile, imageFile.name);

        setBusy(true);
        try {
            const response = await fetch($("chatForm").action, { method: "POST", body: formData });
            const data = await response.json();
            updateMessage(thinking.id, data.response || "No response.", {
                actionUrl: data.action_url || "",
                actionLabel: data.action_label || "",
            });
        } catch (error) {
            updateMessage(thinking.id, "Request failed. Please check the server and try again.");
        } finally {
            setBusy(false);
            if (!options.keepImage) clearImage();
        }
    }

    function regenerateFromMessage(messageId) {
        const message = messageById(messageId);
        if (!message) return;
        const promptMessage = message.role === "user" ? message : previousUserMessage(messageId);
        if (!promptMessage || !promptMessage.text) return;
        sendPrompt(promptMessage.text, { skipUserBubble: true });
    }

    function autoResizeInput() {
        const input = $("chatInput");
        if (!input) return;
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 160) + "px";
    }

    function exportChat() {
        const lines = state.messages.map((message) => {
            const who = message.role === "user" ? "You" : "Assistant";
            return `[${who}]\n${message.text || ""}\n${message.actionUrl ? "Action: " + message.actionUrl + "\n" : ""}`;
        }).join("\n");
        const blob = new Blob([lines], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "tamestorage-chat.txt";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 500);
    }

    function setComposerText(text) {
        const input = $("chatInput");
        if (!input) return;
        input.value = text || "";
        input.focus();
        autoResizeInput();
    }

    window.addEventListener("beforeunload", () => {
        imageUrlsToRevoke.forEach((url) => URL.revokeObjectURL(url));
    });

    document.addEventListener("DOMContentLoaded", () => {
        const form = $("chatForm");
        const input = $("chatInput");
        const imageInput = $("imageInput");
        const dropZone = $("chatDropZone");
        const optionsDisclosure = $("chatOptionsDisclosure");
        if (optionsDisclosure && window.matchMedia("(max-width: 720px)").matches) {
            optionsDisclosure.removeAttribute("open");
        }

        loadHistory();
        renderMessages();
        scrollToBottom();

        imageInput?.addEventListener("change", () => setImageFile(imageInput.files?.[0] || null));
        $("removeImageBtn")?.addEventListener("click", clearImage);
        $("scrollBottomBtn")?.addEventListener("click", scrollToBottom);
        $("exportChatBtn")?.addEventListener("click", exportChat);
        $("clearChatBtn")?.addEventListener("click", () => {
            if (!confirm("Clear this chat history in this browser?")) return;
            try {
                localStorage.removeItem(STORAGE_KEY);
            } catch (_error) {
                // Continue by clearing only the in-memory conversation.
            }
            state.messages = [];
            loadHistory();
            renderMessages();
            scrollToBottom();
        });
        $("chatSearchInput")?.addEventListener("input", renderMessages);

        document.querySelectorAll(".quick-prompt").forEach((button) => {
            button.addEventListener("click", () => setComposerText(button.dataset.prompt || button.textContent.trim()));
        });

        document.querySelectorAll(".command-chip").forEach((button) => {
            button.addEventListener("click", () => setComposerText(button.dataset.template || ""));
        });

        input?.addEventListener("input", autoResizeInput);
        input?.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                form?.requestSubmit();
            }
        });

        document.addEventListener("paste", (event) => {
            const imageItem = Array.from(event.clipboardData?.items || []).find((item) => item.type.startsWith("image/"));
            if (!imageItem) return;
            const file = imageItem.getAsFile();
            if (file) setImageFile(new File([file], "pasted-image.png", { type: file.type }));
        });

        ["dragenter", "dragover"].forEach((eventName) => {
            dropZone?.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropZone.classList.add("dragging");
            });
        });
        ["dragleave", "drop"].forEach((eventName) => {
            dropZone?.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropZone.classList.remove("dragging");
            });
        });
        dropZone?.addEventListener("drop", (event) => {
            const image = Array.from(event.dataTransfer?.files || []).find((file) => file.type.startsWith("image/"));
            if (image) setImageFile(image);
        });

        form?.addEventListener("submit", async (event) => {
            event.preventDefault();
            const text = input ? input.value.trim() : "";
            const imageFile = state.imageFile;
            if (!text && !imageFile) return;
            if (input) {
                input.value = "";
                autoResizeInput();
            }
            await sendPrompt(text, { imageFile });
        });
    });
})();
