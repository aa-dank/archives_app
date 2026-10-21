/* Shared initialization; the versioned Plotly CDN bundle must load first. */
"use strict";

document.querySelectorAll(".archiving-chart-data").forEach(function (source) {
    const container = document.getElementById(source.dataset.chartId);
    function showError(error) {
        container.textContent = "The chart could not load. Please reload the page.";
        container.setAttribute("role", "alert");
        console.error("Could not render archiving chart", error);
    }
    try {
        const figure = JSON.parse(source.textContent);
        Plotly.newPlot(container, figure.data, figure.layout, {
            responsive: true,
            displaylogo: false,
            showTips: false,
            scrollZoom: false,
            modeBarButtonsToRemove: ["select2d", "lasso2d"]
        }).catch(showError);
    } catch (error) {
        showError(error);
    }
});
