/* Shared submission feedback for Inbox Item and Batch Process Inbox. */
"use strict";

document.querySelectorAll("form[data-archive-loading]").forEach(function (form) {
    const button = form.querySelector("[data-archive-submit]");
    const originalLabel = button.value;
    let pending = false;
    let timer = null;

    function reset() {
        clearTimeout(timer);
        timer = null;
        pending = false;
        button.value = originalLabel;
        button.disabled = false;
        button.removeAttribute("aria-busy");
    }

    form.addEventListener("submit", function (event) {
        // Download Copy retains its native submission, including its submitter value.
        if (event.defaultPrevented || (event.submitter && event.submitter !== button)) {
            return;
        }
        event.preventDefault();
        if (pending) {
            return;
        }
        pending = true;
        button.value = "Archiving...";
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        timer = setTimeout(function () {
            timer = null;
            HTMLFormElement.prototype.submit.call(form);
        }, 500);
    });

    window.addEventListener("pagehide", reset);
    window.addEventListener("pageshow", reset);
});
