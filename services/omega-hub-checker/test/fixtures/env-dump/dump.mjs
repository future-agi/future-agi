// Ships every environment variable somewhere: the shape OURS §6.3 worried about.
export const everything = () => JSON.stringify(process.env);
export const names = () => Object.keys(process.env);
export const copy = () => ({ ...process.env });
