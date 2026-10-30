(function () {
    const STORAGE_KEY = "tamestorage.ai.chat.v5";
    const imageUrlsToRevoke = new Set();
    const MAX_IMAGES = 8, MAX_IMAGE_BYTES = 20 * 1024 * 1024, MAX_BATCH_BYTES = 40 * 1024 * 1024;
    const storedImageUrl = url => /^https:\/\/server\.tailb7f57c\.ts\.net\/obsidian-images\/[0-9a-f]{64}\.(png|jpg|gif|webp|avif)$/.test(url || '');
    const imagesOf = message => Array.isArray(message.images) ? message.images : message.imageName ? [{name: message.imageName, url: message.imageUrl || ''}] : [];
    const archiveTimers=new Map();
    const state = {
        messages: [],
        attachments: [],
        busy: false,
        retryMessageId: "",
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

    function inlineAssistantMarkdown(value) {
        return value
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    }

    function formatAssistantText(text) {
        const codeBlocks = [];
        const escaped = escapeHtml(text || "").replace(/```(?:[a-z0-9_-]+)?\s*\n?([\s\S]*?)```/gi, function (_, code) {
            const index = codeBlocks.push(`<pre><code>${code.trim()}</code></pre>`) - 1;
            return `@@TAME_CODE_${index}@@`;
        });
        const html = [];
        let paragraph = [];
        let listType = "";

        function flushParagraph() {
            if (!paragraph.length) return;
            html.push(`<p>${paragraph.join("<br>")}</p>`);
            paragraph = [];
        }

        function closeList() {
            if (!listType) return;
            html.push(`</${listType}>`);
            listType = "";
        }

        escaped.split(/\r?\n/).forEach((rawLine) => {
            const line = rawLine.trim();
            const codeMatch = line.match(/^@@TAME_CODE_(\d+)@@$/);
            if (codeMatch) {
                flushParagraph();
                closeList();
                html.push(codeBlocks[Number(codeMatch[1])] || "");
                return;
            }
            if (!line) {
                flushParagraph();
                closeList();
                return;
            }

            const heading = line.match(/^(#{1,3})\s+(.+)$/);
            if (heading) {
                flushParagraph();
                closeList();
                const level = Math.min(4, heading[1].length + 2);
                html.push(`<h${level}>${inlineAssistantMarkdown(heading[2])}</h${level}>`);
                return;
            }

            const unordered = line.match(/^[-*]\s+(.+)$/);
            const ordered = line.match(/^\d+[.)]\s+(.+)$/);
            if (unordered || ordered) {
                flushParagraph();
                const nextType = unordered ? "ul" : "ol";
                if (listType !== nextType) {
                    closeList();
                    listType = nextType;
                    html.push(`<${listType}>`);
                }
                html.push(`<li>${inlineAssistantMarkdown((unordered || ordered)[1])}</li>`);
                return;
            }

            closeList();
            paragraph.push(inlineAssistantMarkdown(line));
        });

        flushParagraph();
        closeList();
        return html.join("");
    }

    function newId() {
        return crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random());
    }

    function saveHistory() {
        const serializable = state.messages.map((message) => ({
            id: message.id,
            role: message.role,
            scope:message.scope||"",
            text: message.text,
            images: imagesOf(message).map(image => ({name: image.name, url: storedImageUrl(image.url) ? image.url : ""})),
            actionUrl: message.actionUrl || "",
            actionLabel: message.actionLabel || "",
            workspaceJob:message.workspaceJob||"",workspaceReceipt:message.workspaceReceipt||"",workspaceState:message.workspaceState||"",workspaceActivity:message.workspaceActivity||null,workspaceRequestId:message.workspaceRequestId||"",
            archiveSources: message.archiveSources || [], archiveJob: message.archiveJob || "", archiveState: message.archiveState || "", provider: message.provider || "",
            kind: message.kind || "",
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
            if (Array.isArray(parsed)) state.messages = parsed.map(message => ({...message, images: imagesOf(message).map(image => ({name: String(image.name || "Image"), url: storedImageUrl(image.url) ? image.url : ""}))}));
        } catch (error) {
            state.messages = [];
        }
        if (!state.messages.length) {
            state.messages = [{
                id: newId(),
                role: "assistant",
                kind: "welcome",
                text: "How can I help?",
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
            archiveMode: $("archiveMode")?.value || "workspace",
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
            if (state.messages[i]?.role === "user" && (state.messages[i]?.text || imagesOf(state.messages[i]).length)) return state.messages[i];
        }
        return null;
    }

    function textWithSources(message){
        const sources=(message.archiveSources||[]).filter(s=>/^\/admin\/chat-archive\//.test(s.url||''));
        return (message.text||'')+(sources.length?'\n\n'+sources.map((s,i)=>'['+(i+1)+'] '+(s.title||'Untitled')+' · '+(s.created||'').slice(0,10)+' · '+s.role+'\n'+(s.quote||s.text||'')+'\n'+location.origin+s.url).join('\n\n'):'');
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

        if (message.kind === "welcome" || message.kind === "thinking") return actions;

        actions.appendChild(makeSmallButton('<i class="fa-regular fa-copy"></i> Copy', "Copy message", async (event) => {
            const button = event.currentTarget;
            const text = textWithSources(message);
            try {
                await navigator.clipboard.writeText(text);
                button.innerHTML = '<i class="fa-solid fa-check"></i> Copied';
                setTimeout(() => { button.innerHTML = '<i class="fa-regular fa-copy"></i> Copy'; }, 1200);
            } catch (error) {
                window.prompt("Copy message", text);
            }
        }));

        if(message.workspaceJob){
            actions.appendChild(makeSmallButton('<i class="fa-solid fa-stop"></i> Stop',"Stop Workspace task",()=>cancelWorkspace(message)));
        }else actions.appendChild(makeSmallButton('<i class="fa-solid fa-rotate-right"></i> Regenerate', "Regenerate from this message", () => {
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
        const hasConversation=state.messages.some(message=>message.kind!=='welcome');
        document.querySelector('.chat-page-shell')?.classList.toggle('has-conversation',hasConversation);

        state.messages.forEach((message) => {
            if (message.kind==='welcome' && hasConversation) return;
            const text = message.text || "";
            const isMatch = query && text.toLowerCase().includes(query);
            if (query && !isMatch) return;

            const wrapper = document.createElement("div");
            wrapper.className = "message " + message.role + (isMatch ? " search-hit" : "");
            wrapper.dataset.messageId = message.id;

            const avatar = document.createElement("span");
            avatar.className = "message-avatar";
            avatar.innerHTML = message.role === "user" ? '<i class="fa-solid fa-user"></i>' : '<i class="fa-solid fa-wand-magic-sparkles"></i>';

            const content = document.createElement("div");
            content.className = "message-content";

            const images = imagesOf(message);
            if (images.length) {
                const list = document.createElement("ul");
                list.className = "message-images";
                list.setAttribute("aria-label", "Attached images");
                images.forEach(attachment => {
                    const item = document.createElement("li");
                    if (attachment.url) {
                        const link = document.createElement("a");
                        link.href = attachment.url;link.target = "_blank";link.rel = "noopener";
                        link.setAttribute("aria-label", "Open " + attachment.name);
                        const image = document.createElement("img");
                        image.className = "message-image";image.src = attachment.url;
                        image.alt = attachment.name || "Attached image";image.loading = "lazy";
                        link.appendChild(image);item.appendChild(link);
                    } else {
                        item.className = "message-attachment-note";item.textContent = attachment.name;
                    }
                    list.appendChild(item);
                });
                content.appendChild(list);
            }

            const body = document.createElement("div");
            body.className = "message-body";
            if (message.kind === "welcome") {
                wrapper.classList.add("welcome-message");
                body.innerHTML = `<span class="welcome-orb"><i class="fa-solid fa-wand-magic-sparkles"></i></span><strong class="welcome-title">${escapeHtml(text)}</strong><span class="welcome-hint">Ask a question or choose a task.</span>`;
            } else if (message.kind === "thinking") {
                wrapper.classList.add("thinking-message");
                body.innerHTML = '<span class="thinking-dots" aria-label="Assistant is working"><i></i><i></i><i></i></span><span>Thinking…</span>';
            } else if (message.role === "assistant") {
                body.innerHTML = formatAssistantText(text);
            } else {
                body.textContent = text || (images.length ? "Attached images" : "");
            }
            content.appendChild(body);
            if(message.archiveSources?.length){
                const details=document.createElement('details'),summary=document.createElement('summary');
                summary.className='archive-source-summary';summary.textContent=message.archiveSources.length+(message.archiveSources.length===1?' source':' sources');details.append(summary);
                const list=document.createElement('ol');list.className='archive-source-list';
                message.archiveSources.forEach((source,index)=>{
                    if(!/^\/admin\/chat-archive\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+(?:\?page=\d+)?#m-[A-Za-z0-9_-]+$/.test(source.url||''))return;
                    const li=document.createElement('li'),link=document.createElement('a'),meta=document.createElement('small'),quote=document.createElement('blockquote');
                    link.href=source.url;link.target='_blank';link.rel='noopener';link.textContent='['+(index+1)+'] '+(source.title||'Untitled');
                    meta.textContent=(source.created||'').slice(0,10)+' · '+(source.role==='user'?'You':source.role);quote.textContent=source.quote||source.text||'';
                    li.append(link,meta,quote);list.append(li);
                });details.append(list);content.append(details);
            }
            if(message.workspaceJob){const status=document.createElement('small');status.className='archive-status';const a=message.workspaceActivity;status.textContent=(message.provider||'Cyborg')+(a?.total?' · '+a.completed+'/'+a.total:'');status.title=a?.title||'';content.append(status);}
            if(message.archiveState==='queued'){const status=document.createElement('small');status.className='archive-status';status.textContent='Waiting for Cyborg';content.append(status);}
            content.appendChild(makeMessageActions(message));
            wrapper.appendChild(avatar);
            wrapper.appendChild(content);
            container.appendChild(wrapper);
        });
        if (!container.children.length && query) {
            const empty=document.createElement('p');
            empty.className='empty-state';empty.textContent='No messages match your search.';container.appendChild(empty);
        }
    }

    function addMessage(role, text, options = {}) {
        const message = {
            id: newId(),
            role,
            scope:options.scope||"",
            text: text || "",
            images: options.images || [],
            actionUrl: options.actionUrl || "",
            actionLabel: options.actionLabel || "",
            kind: options.kind || "",
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
        message.kind = options.kind || "";
        for(const key of ['scope','archiveSources','archiveJob','archiveState','provider','workspaceJob','workspaceReceipt','workspaceState','workspaceActivity','workspaceRequestId']){
            if(Object.hasOwn(options,key))message[key]=options[key];
        }
        saveHistory();
        renderMessages();
        scrollToBottom();
    }

    function setBusy(isBusy) {
        state.busy = isBusy;
        const button = $("sendButton");
        if (!button) return;
        button.disabled = isBusy;
        button.setAttribute("aria-label", isBusy ? "Sending message" : "Send message");
        button.innerHTML = isBusy ? '<i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i><span>Sending</span>' : '<i class="fa-solid fa-arrow-up" aria-hidden="true"></i><span>Send</span>';
        updateSendReadiness();
    }

    let previewChain = Promise.resolve();

    function updateSendReadiness() {
        const pending = state.attachments.some(image => image.previewState !== "ready");
        if ($("sendButton")) {$("sendButton").disabled = state.busy || pending;$("sendButton").title = pending ? "Preparing image previews" : "Send message";}
    }

    async function prepareImagePreview(attachment) {
        if (attachment.previewState === "converting") return;
        if (attachment.previewTried) {attachment.previewState = "error";updateSendReadiness();return;}
        attachment.previewTried = true;attachment.previewState = "converting";
        attachment.controller = new AbortController();updateSendReadiness();
        $("imagePreviews")?.querySelector(`[data-attachment-id="${attachment.id}"]`)?.classList.add("is-preparing");
        const previousPreview = previewChain;let releasePreview;
        previewChain = new Promise(resolve => {releasePreview = resolve;});
        try {
            await previousPreview;
            if (attachment.controller.signal.aborted || !state.attachments.includes(attachment)) return;
            const data = new FormData();data.append("preview", "1");data.append("csrf", $("chatForm").elements.csrf?.value || "");
            data.append("image", attachment.file, attachment.name);
            const response = await fetch($("chatForm").action, {method: "POST", body: data, signal: attachment.controller.signal});
            if (!response.ok) {const error = await response.json().catch(() => ({}));throw Error(error.response || "Could not prepare this image.");}
            if (!response.headers.get("Content-Type")?.startsWith("image/")) throw Error("Image preview unavailable.");
            const blob = await response.blob();
            if (!state.attachments.includes(attachment)) return;
            const previous = attachment.url;attachment.url = URL.createObjectURL(blob);imageUrlsToRevoke.add(attachment.url);
            attachment.previewState = "loading";releaseImageUrl(previous);renderAttachments();
        } catch (error) {
            if (error.name !== "AbortError") {attachment.previewState = "error";attachmentStatus(error.message + " Remove it or choose a PNG/JPEG copy.");}
        } finally {
            releasePreview();attachment.controller = null;updateSendReadiness();
        }
    }

    function attachmentStatus(text = "") {
        const status = $("attachmentStatus");
        if (status) { status.textContent = text;status.hidden = !text; }
    }

    function releaseImageUrl(url) {
        if (!url?.startsWith("blob:") || state.attachments.some(image => image.url === url) ||
            state.messages.some(message => imagesOf(message).some(image => image.url === url))) return;
        URL.revokeObjectURL(url);imageUrlsToRevoke.delete(url);
    }

    function renderAttachments() {
        const list = $("imagePreviews");
        if (!list) return;
        list.replaceChildren();list.hidden = !state.attachments.length;
        state.attachments.forEach(attachment => {
            const item = document.createElement("li");item.dataset.attachmentId = attachment.id;
            if (attachment.previewState === "converting") item.classList.add("is-preparing");
            const link = document.createElement("a");
            link.href = attachment.url;link.target = "_blank";link.rel = "noopener";
            link.title = attachment.name;link.setAttribute("aria-label", "Preview " + attachment.name);
            const image = document.createElement("img");
            image.src = attachment.url;image.alt = attachment.name;image.decoding = "async";
            image.addEventListener("load", () => {attachment.previewState = "ready";item.classList.remove("is-preparing");updateSendReadiness();});
            image.addEventListener("error", async () => {await prepareImagePreview(attachment);if (attachment.previewState === "error") {image.hidden = true;link.textContent = attachment.name;link.classList.add("preview-unavailable");item.classList.remove("is-preparing");}});
            link.appendChild(image);item.appendChild(link);
            const remove = document.createElement("button");
            remove.type = "button";remove.className = "chat-image-remove";
            remove.setAttribute("aria-label", "Remove " + attachment.name);
            remove.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
            remove.addEventListener("click", () => {
                const index = state.attachments.indexOf(attachment);attachment.controller?.abort();
                state.attachments = state.attachments.filter(image => image.id !== attachment.id);
                releaseImageUrl(attachment.url);renderAttachments();attachmentStatus();
                const buttons = list.querySelectorAll("button");
                (buttons[Math.min(index, buttons.length - 1)] || $("attachImagesBtn"))?.focus();
            });
            item.appendChild(remove);list.appendChild(item);
        });
        updateSendReadiness();
        if ($("imageLabel")) $("imageLabel").textContent = state.attachments.length ? `${state.attachments.length} image${state.attachments.length === 1 ? "" : "s"}` : "Images";
        $("attachImagesBtn")?.setAttribute("aria-label", "Attach images" + (state.attachments.length ? ` (${state.attachments.length} selected)` : ""));
    }

    function addImageFiles(files) {
        let total = state.attachments.reduce((sum, image) => sum + image.file.size, 0), error = "";
        for (const file of files) {
            if ((!file.type.startsWith("image/") && !/\.(png|jpe?g|gif|webp|avif|heic|heif|bmp|tiff?)$/i.test(file.name)) || /svg/i.test(file.type)) { error = "Choose image files such as PNG, JPEG or HEIC.";continue; }
            if (state.attachments.some(image => image.file.name === file.name && image.file.size === file.size && image.file.lastModified === file.lastModified)) continue;
            if (state.attachments.length >= MAX_IMAGES) { error = "Attach up to 8 images per message.";continue; }
            if (!file.size) { error = "An image is empty. Choose another file.";continue; }
            if (file.size > MAX_IMAGE_BYTES) { error = "Each image must be under 20 MB.";continue; }
            if (total + file.size > MAX_BATCH_BYTES) { error = "Keep the images under 40 MB combined.";continue; }
            const url = URL.createObjectURL(file);imageUrlsToRevoke.add(url);
            state.attachments.push({id: newId(), file, url, name: file.name, previewState: "loading"});total += file.size;
        }
        renderAttachments();attachmentStatus(error);
    }

    function clearImages() {
        const previous = state.attachments;state.attachments = [];
        previous.forEach(image => {image.controller?.abort();releaseImageUrl(image.url);});
        if ($("imageInput")) $("imageInput").value = "";
        renderAttachments();attachmentStatus();
    }

    function applyResponse(messageId,data,requestId){
            const receipts = Array.isArray(data.actions) ? data.actions : [], lastAction = receipts.at(-1);
            const original = messageById(requestId);
            if (original) {
                const attached=imagesOf(original);
                for(const photo of receipts.filter(a=>a.action==="diary.saved")){
                    const stored=photo.image_urls||(photo.image_url?[photo.image_url]:[]);
                    const numbers=Array.isArray(photo.image_numbers)?photo.image_numbers:stored.length===attached.length?attached.map((_,i)=>i+1):[];
                    if(numbers.length!==stored.length||!stored.every(storedImageUrl))continue;
                    numbers.forEach((number,index)=>{
                        const image=attached[number-1];if(!image)return;
                        const previous=image.url;image.url=stored[index];delete image.file;releaseImageUrl(previous);
                    });
                }
            }
            const receiptText = receipts.length && !data.workspace_result ? "\n\n" + receipts.map(a => "✓ " + a.action.replaceAll(".", " · ") + ": " + a.title).join("\n") : "";
            updateMessage(messageId, (data.response || "No response.") + receiptText, {
                scope:messageById(messageId)?.scope||(data.workspace_result||data.workspace_job?"workspace":"archive"),
                actionUrl: data.action_url || lastAction?.url || "", actionLabel: data.action_label || (lastAction ? "View changes" : ""),
                archiveSources: data.archive_sources || [], archiveJob: data.archive_job || "", archiveState: data.archive_state || "", provider: data.provider || "",
                workspaceReceipt: data.workspace_job || messageById(messageId)?.workspaceJob || messageById(messageId)?.workspaceReceipt || "",
                workspaceJob: data.workspace_job || "", workspaceState: data.workspace_state || "", workspaceActivity:data.activity||null, workspaceRequestId: requestId,
            });
            if(data.archive_job)pollArchive(messageId,data.archive_job);
            if(data.workspace_job)pollWorkspace(messageId,data.workspace_job);
    }

    async function sendPrompt(promptText, options = {}) {
        const message = String(promptText || "").trim(), images = options.images || [];
        if (state.busy || (!message && !images.length)) return;
        const scope=isWorkspace(getPreferences().archiveMode)?"workspace":"archive";
        let userMessage;
        if (!options.skipUserBubble) userMessage = addMessage("user", message, {images,scope});
        const thinking = addMessage("assistant", "", {kind: "thinking",scope});
        const formData = new FormData();
        formData.append("msg", message);
        const requestId = options.requestId || userMessage?.id || newId();
        formData.append("request_id", requestId);
        formData.append("csrf", $("chatForm").elements.csrf?.value || "");
        formData.append("prompt", $("imagePromptInput")?.value || "Describe the attached images.");
        const prefs = getPreferences();
        formData.append("include_context", prefs.includeContext);formData.append("file_path", prefs.filePath);
        formData.append("detail_level", prefs.detailLevel);formData.append("response_style", prefs.responseStyle);
        formData.append("context_jobs",JSON.stringify(state.messages.filter(m=>m.role==="assistant"&&(m.workspaceJob||m.workspaceReceipt)).slice(-4).map(m=>m.workspaceJob||m.workspaceReceipt)));
        const pendingRequests=new Set(state.messages.filter(m=>m.workspaceJob).map(m=>m.workspaceRequestId));
        formData.append("history",JSON.stringify(state.messages.filter(m=>m.scope==="workspace"&&!pendingRequests.has(m.id)&&m.kind!=="thinking"&&m.kind!=="welcome"&&m.id!==requestId&&!m.workspaceJob).slice(-10).map(m=>({role:m.role,text:(m.text||"").slice(0,1200)}))));
        formData.append("archive_mode",prefs.archiveMode);if(options.regenerate)formData.append("archive_refresh","1");
        images.forEach(image => formData.append("image", image.file, image.name));
        setBusy(true);
        try {
            const response = await fetch($("chatForm").action, {method: "POST", body: formData,headers:{"X-Workspace-Client":"1"}});
            const data = await response.json().catch(() => ({response: "The upload could not finish. Try fewer or smaller images."}));
            if (!response.ok) throw new Error(data.response || "Request failed");
            applyResponse(thinking.id,data,requestId);
        } catch (error) {
            updateMessage(thinking.id, (error.message || "Request failed.") + " Use Regenerate to retry this message.");
        } finally {
            setBusy(false);
        }
    }

    async function regenerateFromMessage(messageId) {
        if (state.busy) return;
        const message = messageById(messageId);
        if (!message) return;
        const promptMessage = message.role === "user" ? message : previousUserMessage(messageId);
        if (!promptMessage || (!promptMessage.text && !imagesOf(promptMessage).length)) return;
        const pending=state.messages.find(m=>m.workspaceJob&&m.workspaceRequestId===promptMessage.id);
        if(pending){pollWorkspace(pending.id,pending.workspaceJob);scrollToBottom();return;}
        const images = [];
        try {
            for (const image of imagesOf(promptMessage)) {
                if (image.file) { images.push(image);continue; }
                if (!image.url) throw Error("Reattach images");
                const response = await fetch(storedImageUrl(image.url) ? new URL(image.url).pathname : image.url);
                if (!response.ok) throw Error("Image unavailable");
                const blob = await response.blob();
                images.push({...image, file: new File([blob], image.name, {type: blob.type})});
            }
        } catch (_error) {
            clearImages();setComposerText(promptMessage.text || "");state.retryMessageId = promptMessage.id;
            attachmentStatus(`Reattach ${imagesOf(promptMessage).length} image(s) to retry this message.`);
            $("imageInput")?.click();return;
        }
        await sendPrompt(promptMessage.text, {skipUserBubble: true, requestId: promptMessage.id, images,regenerate:true});
    }

    async function pollWorkspace(messageId,key){
        const timerKey="workspace:"+key;
        if(archiveTimers.has(timerKey)||document.hidden)return;
        const timer=setTimeout(async()=>{
            archiveTimers.delete(timerKey);
            if(document.hidden||messageById(messageId)?.workspaceJob!==key)return;
            try{
                const response=await fetch('/chat/jobs/'+encodeURIComponent(key));
                if(response.status===404){updateMessage(messageId,'This request is no longer available.',{workspaceJob:'',workspaceState:''});return;}
                if(response.ok){
                    const data=await response.json();
                    if(data.result){applyResponse(messageId,data.result,messageById(messageId)?.workspaceRequestId);return;}
                    const current=messageById(messageId);
                    const text=(data.cancel_requested?'Stopping after the current step…':data.activity?.step||(data.status==='running'?'Working on your request…':'Waiting for '+data.provider))+(data.progress_response?'\n\n'+data.progress_response:'');
                    if(current&&(current.workspaceState!==data.status||current.text!==text))updateMessage(messageId,text,{workspaceState:data.status,workspaceActivity:data.activity||null});
                }
            }catch(_error){}
            if(messageById(messageId)?.workspaceJob===key)pollWorkspace(messageId,key);
        },3000);
        archiveTimers.set(timerKey,timer);
    }

    async function cancelWorkspace(message){
        const formData=new FormData();formData.append('csrf',$("chatForm").elements.csrf?.value||'');
        try{
            const response=await fetch('/chat/jobs/'+encodeURIComponent(message.workspaceJob)+'/cancel',{method:'POST',body:formData});
            if(!response.ok)throw Error('Stop request failed');
            const data=await response.json();
            if(data.result)applyResponse(message.id,data.result,message.workspaceRequestId);
            else updateMessage(message.id,'Stopping after the current step…');
        }catch(_error){updateMessage(message.id,'Could not request a stop. The task is still active; try again.');}
    }

    async function pollArchive(messageId,key){
        if(archiveTimers.has(key)||document.hidden)return;
        const timer=setTimeout(async()=>{
            archiveTimers.delete(key);if(document.hidden)return;
            try{const response=await fetch('/admin/chat-archive/jobs/'+encodeURIComponent(key));if(!response.ok)return;
                const data=await response.json();if(data.status==='ready'){
                    updateMessage(messageId,data.result.response,{archiveSources:data.result.archive_sources||[],archiveJob:'',archiveState:'',provider:data.result.provider});return;
                }if(data.status==='failed'){updateMessage(messageId,'The source changed. Ask again for current evidence.',{archiveJob:'',archiveState:''});return;}
            }catch(_error){}if(messageById(messageId)?.archiveJob===key)pollArchive(messageId,key);
        },5000);archiveTimers.set(key,timer);
    }
    document.addEventListener('visibilitychange',()=>{if(document.hidden){archiveTimers.forEach(clearTimeout);archiveTimers.clear();}else{state.messages.filter(m=>m.archiveJob).forEach(m=>pollArchive(m.id,m.archiveJob));state.messages.filter(m=>m.workspaceJob).forEach(m=>pollWorkspace(m.id,m.workspaceJob));}});

    function autoResizeInput() {
        const input = $("chatInput");
        if (!input) return;
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 160) + "px";
    }

    function exportChat() {
        const lines = state.messages.map((message) => {
            const who = message.role === "user" ? "You" : "Assistant";
            return `[${who}]\n${textWithSources(message)}\n${message.actionUrl ? "Action: " + message.actionUrl + "\n" : ""}`;
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
        state.retryMessageId = "";
        const input = $("chatInput");
        if (!input) return;
        input.value = text || "";
        input.focus();
        autoResizeInput();
    }

    function selectedContextPath() {
        return $("fileContextPath")?.value || "";
    }

    let contextSearchTimer;
    let contextSearchController;

    async function searchContextFiles() {
        const search = $("fileContextSearch");
        const select = $("fileContextPath");
        const status = $("fileContextSearchStatus");
        if (!search || !select || !status) return;
        const query = search.value.trim();
        const selected = select.value;
        if (contextSearchController) contextSearchController.abort();
        contextSearchController = new AbortController();
        let paths = [];
        if (query.length >= 2) {
            status.textContent = "Searching…";
            try {
                const response = await fetch(`/chat/files?q=${encodeURIComponent(query)}`, {signal:contextSearchController.signal});
                if (!response.ok) throw new Error("Search is unavailable");
                paths = (await response.json()).items || [];
            } catch (error) {
                if (error.name === "AbortError") return;
                status.textContent = "Could not search files. Try again.";
                return;
            }
        }
        const options = [new Option("Choose automatically", "")];
        const matchCount = paths.length;
        if (selected && !paths.includes(selected)) paths.unshift(selected);
        paths.forEach(path => options.push(new Option(path, path)));
        select.replaceChildren(...options);
        select.value = selected;
        status.textContent = query.length < 2
            ? "Search by name, then select a file."
            : matchCount ? `${matchCount} matching file${matchCount === 1 ? "" : "s"}` : "No readable files found.";
        updateContextUI();
    }

    function isWorkspace(mode){return mode==="workspace"||mode==="workspace_google";}

    function updateContextUI() {
        const archiveMode=$("archiveMode")?.value||"workspace";
        const modeSelect=$("archiveMode");
        if(modeSelect)modeSelect.title=(isWorkspace(archiveMode)?'Workspace':'Chat archive')+' · '+(archiveMode==='workspace_google'||archiveMode==='google'?'Google':archiveMode==='excerpts'?'Excerpts':'Cyborg');
        const path = selectedContextPath();
        const toolsEnabled = $("includeTreeContext")?.checked !== false;
        const scope = $("conversationScope");
        const composer = $("composerContext");
        const help = $("fileContextHelp");
        if (scope) scope.textContent = !isWorkspace(archiveMode) ? "Chat archive" : path ? `Reading ${path}` : (toolsEnabled ? "Files, calendar & tasks" : "Private context off");
        if (composer) {
            composer.innerHTML = !isWorkspace(archiveMode) ? `<i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i> Archive` : path
                ? `<i class="fa-regular fa-file-lines" aria-hidden="true"></i> ${escapeHtml(path.split("/").pop())}`
                : `<i class="fa-solid ${toolsEnabled ? "fa-wand-magic-sparkles" : "fa-lock"}" aria-hidden="true"></i> ${toolsEnabled ? "Automatic" : "Private context off"}`;
            composer.title = path || (toolsEnabled ? "The assistant requests only the context it needs" : "Private tools disabled");
        }
        if (help) {
            const toolSummary = help.dataset.toolSummary || "Workspace, calendar, and task tools are available.";
            const helpText = path
                ? `Reading ${path} for this request.`
                : (toolsEnabled ? toolSummary : "Private workspace, calendar, and task tools are disabled for this request.");
            help.classList.toggle("has-file", Boolean(path));
            help.setAttribute("aria-label", helpText);
            help.innerHTML = path
                ? `<i class="fa-solid fa-check" aria-hidden="true"></i><span class="context-scope-note-text">Reading <strong>${escapeHtml(path)}</strong> for this request.</span>`
                : `<i class="fa-solid fa-circle-info" aria-hidden="true"></i><span class="context-scope-note-text">${escapeHtml(helpText)}</span>`;
        }
        document.querySelectorAll(".quick-prompt.requires-file").forEach((button) => {
            button.classList.toggle("needs-context", !path);
        });
    }

    window.addEventListener("beforeunload", () => {
        state.attachments.forEach(image => image.controller?.abort());
        imageUrlsToRevoke.forEach((url) => URL.revokeObjectURL(url));
    });

    document.addEventListener("DOMContentLoaded", () => {
        const form = $("chatForm");
        const input = $("chatInput");
        const imageInput = $("imageInput");
        const dropZone = $("chatDropZone");
        loadHistory();
        renderMessages();
        scrollToBottom();
        updateContextUI();
        state.messages.filter(m=>m.archiveJob).forEach(m=>pollArchive(m.id,m.archiveJob));
        state.messages.filter(m=>m.workspaceJob).forEach(m=>pollWorkspace(m.id,m.workspaceJob));
        $("archiveMode")?.addEventListener("change",updateContextUI);

        imageInput?.addEventListener("change", () => {addImageFiles(Array.from(imageInput.files || []));imageInput.value = "";});
        $("attachImagesBtn")?.addEventListener("click", () => imageInput?.click());
        $("scrollBottomBtn")?.addEventListener("click", scrollToBottom);
        $("exportChatBtn")?.addEventListener("click", exportChat);
        $("clearChatBtn")?.addEventListener("click", async () => {
            const pending=state.messages.filter(m=>m.workspaceJob);
            if (!confirm(pending.length?"Clear this chat and stop its active Workspace tasks? Completed changes are kept.":"Clear this chat history in this browser?")) return;
            try{
                for(const message of pending){
                    const body=new FormData();body.append('csrf',$("chatForm").elements.csrf?.value||'');
                    const response=await fetch('/chat/jobs/'+encodeURIComponent(message.workspaceJob)+'/cancel',{method:'POST',body});
                    if(!response.ok)throw Error('Stop failed');
                }
            }catch(_error){alert('Could not stop active tasks. The chat was kept so you can try again.');return;}
            archiveTimers.forEach(clearTimeout);archiveTimers.clear();
            try {
                localStorage.removeItem(STORAGE_KEY);
            } catch (_error) {
                // Continue by clearing only the in-memory conversation.
            }
            state.messages = [];
            state.retryMessageId = "";
            for (const url of imageUrlsToRevoke) if (!state.attachments.some(image => image.url === url)) {URL.revokeObjectURL(url);imageUrlsToRevoke.delete(url);}
            loadHistory();
            renderMessages();
            scrollToBottom();
        });
        const searchToggle = $("toggleChatSearchBtn");
        const searchToolbar = $("chatSearchToolbar");
        function setSearchOpen(open) {
            if (!searchToolbar || !searchToggle) return;
            searchToolbar.hidden = !open;
            searchToggle.setAttribute("aria-expanded", String(open));
            if (open) {
                $("chatSearchInput")?.focus();
            } else {
                if ($("chatSearchInput")) $("chatSearchInput").value = "";
                renderMessages();
                scrollToBottom();
                searchToggle.focus();
            }
        }
        searchToggle?.addEventListener("click", () => setSearchOpen(searchToolbar?.hidden));
        $("chatSearchInput")?.addEventListener("input", renderMessages);
        searchToolbar?.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                setSearchOpen(false);
            }
        });

        $("fileContextPath")?.addEventListener("change", updateContextUI);
        $("fileContextSearch")?.addEventListener("input", () => {
            clearTimeout(contextSearchTimer);
            if (contextSearchController) contextSearchController.abort();
            contextSearchTimer = setTimeout(searchContextFiles, 240);
        });
        $("includeTreeContext")?.addEventListener("change", updateContextUI);

        document.querySelectorAll(".quick-prompt").forEach((button) => {
            button.addEventListener("click", () => {
                const path = selectedContextPath();
                if (button.classList.contains("requires-file") && !path) {
                    const options = document.querySelector('.chat-options');
                    if (options) options.open = true;
                    $("fileContextSearch")?.focus();
                    $("fileContextHelp")?.classList.add("context-attention");
                    setTimeout(() => $("fileContextHelp")?.classList.remove("context-attention"), 1600);
                    return;
                }
                if($("archiveMode"))$("archiveMode").value=button.dataset.archive||"workspace";updateContextUI();
                setComposerText((button.dataset.prompt || button.textContent.trim()).replaceAll("{file}", path));
                if (button.classList.contains("requires-image") && !state.attachments.length) {
                    imageInput?.click();
                }
            });
        });

        document.querySelectorAll(".command-chip").forEach((button) => {
            button.addEventListener("click", () => {
                let template = button.dataset.template || "";
                const path = selectedContextPath();
                if (path && /\/(open|inspect)\s$/.test(template)) template += path;
                setComposerText(template);
            });
        });

        input?.addEventListener("input", autoResizeInput);
        input?.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                form?.requestSubmit();
            }
        });

        document.addEventListener("paste", event => {
            if (!form?.contains(event.target)) return;
            const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === "file").map(item => item.getAsFile()).filter(Boolean);
            if (!files.length) return;
            event.preventDefault();addImageFiles(files);
        });
        const shell = document.querySelector(".chat-page-shell");
        ["dragenter", "dragover"].forEach(name => shell?.addEventListener(name, event => {event.preventDefault();dropZone?.classList.add("dragging");}));
        shell?.addEventListener("dragleave", event => {if (!shell.contains(event.relatedTarget)) dropZone?.classList.remove("dragging");});
        shell?.addEventListener("drop", event => {event.preventDefault();dropZone?.classList.remove("dragging");addImageFiles(Array.from(event.dataTransfer?.files || []));});

        form?.addEventListener("submit", async event => {
            event.preventDefault();if (state.busy) return;
            const text = input ? input.value.trim() : "", images = [...state.attachments];
            if (!text && !images.length) return;
            if (images.some(image => image.previewState !== "ready")) {attachmentStatus("Wait for the previews, or remove an image that could not be read.");return;}
            const retry = messageById(state.retryMessageId);
            const retrying = retry && text === retry.text.trim();
            if (retrying && images.length !== imagesOf(retry).length) {attachmentStatus(`Reattach all ${imagesOf(retry).length} images to retry.`);return;}
            state.retryMessageId = "";
            if (input) {input.value = "";autoResizeInput();}
            if (retrying) {retry.images = images;saveHistory();}
            const sending = sendPrompt(text, {images, ...(retrying ? {skipUserBubble: true, requestId: retry.id, regenerate:true} : {})});
            // Submitted images now belong to the message. Preserve any next draft added while sending.
            state.attachments = [];imageInput.value = "";renderAttachments();attachmentStatus();
            await sending;
        });
    });
})();
