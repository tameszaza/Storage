(function () {
    const imageUrlsToRevoke = [];

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function formatAssistantText(text) {
        const escaped = escapeHtml(text || "");
        return escaped
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
            .replace(/\n/g, "<br>");
    }

    function addMessage(role, text, options) {
        const container = document.getElementById("chatMessages");
        if (!container) return null;

        const opts = options || {};
        const message = document.createElement("div");
        message.className = "message " + role;

        const avatar = document.createElement("span");
        avatar.className = "message-avatar";
        avatar.innerHTML = role === "user"
            ? '<i class="fa-solid fa-user"></i>'
            : '<i class="fa-solid fa-robot"></i>';

        const bubble = document.createElement("div");
        bubble.className = "message-bubble";

        if (opts.imageUrl) {
            const imageWrap = document.createElement("div");
            imageWrap.className = "message-image-wrap";

            const image = document.createElement("img");
            image.className = "message-image";
            image.src = opts.imageUrl;
            image.alt = opts.imageName || "Attached image";

            imageWrap.appendChild(image);
            bubble.appendChild(imageWrap);
        }

        const body = document.createElement("div");
        body.className = "message-body";
        if (role === "assistant") {
            body.innerHTML = formatAssistantText(text);
        } else {
            body.textContent = text || (opts.imageUrl ? "Attached image" : "");
        }

        bubble.appendChild(body);
        message.appendChild(avatar);
        message.appendChild(bubble);
        container.appendChild(message);
        container.scrollTop = container.scrollHeight;
        return body;
    }

    function setBusy(isBusy) {
        const button = document.getElementById("sendButton");
        if (!button) return;
        button.disabled = isBusy;
        button.innerHTML = isBusy
            ? '<i class="fa-solid fa-circle-notch fa-spin"></i> Sending'
            : '<i class="fa-solid fa-paper-plane"></i> Send';
    }

    function updateImageLabel(imageInput, imageLabel) {
        if (!imageInput || !imageLabel) return;
        const file = imageInput.files && imageInput.files[0];
        imageLabel.textContent = file ? file.name : "Attach image";
        imageLabel.title = file ? file.name : "";
    }

    window.addEventListener("beforeunload", () => {
        imageUrlsToRevoke.forEach((url) => URL.revokeObjectURL(url));
    });

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("chatForm");
        const input = document.getElementById("chatInput");
        const imageInput = document.getElementById("imageInput");
        const imageLabel = document.getElementById("imageLabel");

        if (imageInput && imageLabel) {
            imageInput.addEventListener("change", () => updateImageLabel(imageInput, imageLabel));
        }

        if (!form) return;
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const message = input ? input.value.trim() : "";
            const imageFile = imageInput && imageInput.files ? imageInput.files[0] : null;
            if (!message && !imageFile) return;

            let previewUrl = null;
            if (imageFile) {
                previewUrl = URL.createObjectURL(imageFile);
                imageUrlsToRevoke.push(previewUrl);
            }

            addMessage("user", message, {
                imageUrl: previewUrl,
                imageName: imageFile ? imageFile.name : ""
            });

            const formData = new FormData(form);
            if (input) input.value = "";
            setBusy(true);
            const thinkingBody = addMessage("assistant", "Thinking...");

            try {
                const response = await fetch(form.action, { method: "POST", body: formData });
                const data = await response.json();
                if (thinkingBody) thinkingBody.innerHTML = formatAssistantText(data.response || "No response.");
                form.reset();
                if (imageLabel) imageLabel.textContent = "Attach image";
            } catch (error) {
                if (thinkingBody) thinkingBody.textContent = "Request failed. Please try again.";
            } finally {
                setBusy(false);
            }
        });
    });
})();
