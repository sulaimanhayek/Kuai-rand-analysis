const PAGES = [
  {name: "Memo", path: "./"},
  {name: "Tail gap", path: "./metrics"},
  {name: "Experiment", path: "./experiment"},
  {name: "Segments", path: "./segments"},
  {name: "Methods", path: "./methods"}
];

const current = (path, page) =>
  page.path === "./" ? path === "/" || path === "/index" : path === page.path.slice(1);

export default {
  title: "KuaiRand feed analysis",
  root: "src",
  base: "/projects/kuairand-analysis/",
  style: "style.css",
  sidebar: false,
  search: false,
  pager: false,
  toc: {show: true, label: "On this page"},
  head: `<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Figtree:wght@500;600;700&family=Inter:wght@400;500;600&display=swap">
<meta name="description" content="Should a short-video feed boost under-distributed videos? A causal analysis of KuaiRand's randomly delivered impressions.">`,
  header: ({path}) => `<a class="brand" href="./">KuaiRand feed analysis</a>
<nav>${PAGES.map((p) => `<a href="${p.path}"${current(path, p) ? ` aria-current="page"` : ""}>${p.name}</a>`).join("")}</nav>`,
  footer: `Data: <a href="https://kuairand.com/">KuaiRand-Pure</a> (Gao et al., CIKM 2022). Code MIT, derived data
CC BY-SA 4.0. <a href="https://github.com/sulaimanhayek/Kuai-rand-analysis">Source and pipeline</a>.`
};
