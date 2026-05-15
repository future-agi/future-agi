const path = require('path');

const frontendBin = (tool) =>
  path.join('frontend', 'node_modules', '.bin', tool);

const generatedApiContractsDir = path.join(
  'frontend',
  'src',
  'generated',
  'api-contracts'
);

const isGeneratedApiContract = (file) =>
  file.startsWith(`${generatedApiContractsDir}${path.sep}`) ||
  file.startsWith(`${generatedApiContractsDir}/`);

const quoteFiles = (files) => files.map((f) => `"${f}"`).join(' ');

module.exports = {
  'frontend/src/**/*.{js,jsx,ts,tsx}': (files) => {
    const lintableFiles = files.filter((file) => !isGeneratedApiContract(file));
    if (lintableFiles.length === 0) {
      return [];
    }
    const args = quoteFiles(lintableFiles);
    return [
      `${frontendBin('eslint')} --fix ${args}`,
      `${frontendBin('prettier')} --write ${args}`,
    ];
  },
  'frontend/src/**/*.{json,css,md}': (files) => {
    const args = quoteFiles(files);
    return `${frontendBin('prettier')} --write ${args}`;
  },
};
