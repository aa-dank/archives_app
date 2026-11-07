/* Shared initialization; the versioned Plotly CDN bundle must load first. */
"use strict";

document.querySelectorAll("script[data-plotly-chart]").forEach(function (source) {
    const container = document.getElementById(source.dataset.chartId);
    const controls = Array.from(document.querySelectorAll("[data-plotly-toggle]"))
        .filter(button => button.dataset.chartId === container.id);
    function showError(error) {
        controls.forEach(button => {
            button.disabled = true;
            button.closest("tr").classList.remove("is-toggleable");
        });
        container.textContent = "The chart could not load. Please reload the page.";
        container.setAttribute("role", "alert");
        console.error("Could not render chart", error);
    }
    try {
        const figure = JSON.parse(source.textContent);
        Plotly.newPlot(container, figure.data, figure.layout, {
            responsive: true,
            displaylogo: false,
            showTips: false,
            scrollZoom: false,
            modeBarButtonsToRemove: ["select2d", "lasso2d"]
        }).then(function () {
            controls.forEach(function (button) {
                const index = Number(button.dataset.traceIndex);
                if (!Number.isInteger(index) || !container.data[index] || container.data[index].type !== "bar") {
                    return;
                }
                const row = button.closest("tr");
                let pending = false;
                function syncControl() {
                    const visible = container.data[index].visible !== "legendonly"
                        && container.data[index].visible !== false;
                    button.setAttribute("aria-pressed", String(visible));
                    button.title = visible ? "Hide segment" : "Show segment";
                    row.classList.toggle("is-muted", !visible);
                    pending = false;
                    button.removeAttribute("aria-busy");
                    button.disabled = false;
                }
                syncControl();
                row.classList.add("is-toggleable");
                row.addEventListener("click", function () {
                    if (button.disabled || pending) {
                        return;
                    }
                    pending = true;
                    button.setAttribute("aria-busy", "true");
                    const visible = button.getAttribute("aria-pressed") === "true";
                    Plotly.restyle(container, {visible: visible ? "legendonly" : true}, [index])
                        .then(syncControl).catch(showError);
                });
            });
        }).catch(showError);
    } catch (error) {
        showError(error);
    }
});
