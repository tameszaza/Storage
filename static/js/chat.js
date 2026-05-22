(function () {
    function addMessage(role, text) {
        const container = document.getElementById("chatMessages");
        if (!container) return;
        const message = document.createElement("div");
        message.className = "message " + role;
        message.textContent = text;
        container.appendChild(message);
        container.scrollTop = container.scrollHeight;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("chatForm");
        const input = document.getElementById("chatInput");
        const imageInput = document.getElementById("imageInput");
        if (!form) return;
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const message = input ? input.value.trim() : "";
            const hasImage = imageInput && imageInput.files.length > 0;
            if (!message && !hasImage) return;
            addMessage("user", message || "Image uploaded");
            const formData = new FormData(form);
            if (input) input.value = "";
            addMessage("assistant", "Thinking...");
            const thinkingNode = document.querySelector(".message.assistant:last-child");
            try {
                const response = await fetch(form.action, { method: "POST", body: formData });
                const data = await response.json();
                if (thinkingNode) thinkingNode.textContent = data.response || "No response.";
                if (imageInput) imageInput.value = "";
            } catch (error) {
                if (thinkingNode) thinkingNode.textContent = "Request failed. Please try again.";
            }
        });
    });
})();
