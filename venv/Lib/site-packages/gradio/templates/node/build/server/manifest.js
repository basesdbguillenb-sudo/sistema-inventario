const manifest = (() => {
function __memo(fn) {
	let value;
	return () => value ??= (value = fn());
}

return {
	appDir: "_app",
	appPath: "_app",
	assets: new Set([]),
	mimeTypes: {},
	_: {
		client: {start:"_app/immutable/entry/start.hCqeRE8p.js",app:"_app/immutable/entry/app.D9io_BSX.js",imports:["_app/immutable/entry/start.hCqeRE8p.js","_app/immutable/chunks/B_HNIeX5.js","_app/immutable/chunks/DDfTnx1u.js","_app/immutable/chunks/DzwcWd-r.js","_app/immutable/entry/app.D9io_BSX.js","_app/immutable/chunks/xc8nnbwS.js","_app/immutable/chunks/DDfTnx1u.js","_app/immutable/chunks/DzwcWd-r.js","_app/immutable/chunks/ChTP-j8_.js","_app/immutable/chunks/DsfoWIX0.js"],stylesheets:[],fonts:[],uses_env_dynamic_public:false},
		nodes: [
			__memo(() => import('./chunks/0-C76zIrVF.js')),
			__memo(() => import('./chunks/1-Dv7pOgzZ.js')),
			__memo(() => import('./chunks/2-DoIM9QLI.js').then(function (n) { return n._; }))
		],
		remotes: {
			
		},
		routes: [
			{
				id: "/[...catchall]",
				pattern: /^(?:\/([^]*))?\/?$/,
				params: [{"name":"catchall","optional":false,"rest":true,"chained":true}],
				page: { layouts: [0,], errors: [1,], leaf: 2 },
				endpoint: null
			}
		],
		prerendered_routes: new Set([]),
		matchers: async () => {
			
			return {  };
		},
		server_assets: {}
	}
}
})();

const prerendered = new Set([]);

const base = "";

export { base, manifest, prerendered };
//# sourceMappingURL=manifest.js.map
