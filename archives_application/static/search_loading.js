/* Shared loading feedback for search forms and fresh-search links. */
"use strict";

document.querySelectorAll('[data-search-loading]').forEach(function (button) {
    const originalLabel = button.textContent;
    const isLink = button.tagName === 'A';
    const source = isLink ? button : button.form;
    let pending = false;
    let timer = null;

    if (!source) {
        return;
    }

    function reset() {
        clearTimeout(timer);
        timer = null;
        pending = false;
        button.textContent = originalLabel;
        button.classList.remove('disabled');
        button.removeAttribute('aria-disabled');
        if (!isLink) {
            button.disabled = false;
        }
    }

    source.addEventListener(isLink ? 'click' : 'submit', function (event) {
        if (event.defaultPrevented) {
            return;
        }
        if (isLink && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0)) {
            return;
        }
        event.preventDefault();
        if (pending || button.disabled) {
            return;
        }

        pending = true;
        button.textContent = 'Searching...';
        button.classList.add('disabled');
        button.setAttribute('aria-disabled', 'true');
        if (!isLink) {
            button.disabled = true;
        }
        // Match Archive Search's existing pause so the browser can paint the label.
        timer = setTimeout(function () {
            timer = null;
            if (isLink) {
                window.location.assign(button.href);
            } else {
                HTMLFormElement.prototype.submit.call(source);
            }
        }, 350);
    });

    // Cancel stale navigation timers and recover controls restored by Back/Forward.
    window.addEventListener('pagehide', reset);
    window.addEventListener('pageshow', reset);
});
