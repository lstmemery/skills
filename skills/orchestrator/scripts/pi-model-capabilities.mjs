// Read only installed/configured model metadata. Never create a session or resolve auth.
const [moduleUrl, provider, modelId, packageUrl, modelsPath] = process.argv.slice(2);
const sdk = await import(moduleUrl);
if (typeof sdk.getModel !== "function" || typeof sdk.getSupportedThinkingLevels !== "function") {
  throw new Error("Installed Pi model capability contract changed");
}
let model = sdk.getModel(provider, modelId);
if (packageUrl) {
  const {ModelConfig} = await import(new URL("dist/core/model-config.js", packageUrl));
  const {composeModelProvider} = await import(new URL("dist/core/provider-composer.js", packageUrl));
  const {builtinProviders} = await import(new URL("./providers/all.js", moduleUrl));
  const config = await ModelConfig.load(modelsPath);
  if (config.error) throw new Error("Pi configured model metadata is invalid");
  const base = builtinProviders().find(candidate => candidate.id === provider);
  // Composition creates lazy auth callbacks; invoking only getModels never resolves credentials.
  const composed = composeModelProvider(provider, base, config);
  model = composed.getModels().find(candidate => candidate.id === modelId);
}
if (!model) throw new Error("Exact Pi model missing from installed metadata");
console.log(JSON.stringify({provider, model: modelId, efforts: sdk.getSupportedThinkingLevels(model)}));
