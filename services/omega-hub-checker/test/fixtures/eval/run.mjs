export const run = (source) => eval(source);
export const make = (body) => new Function("x", body);
export const load = (name) => import(name);
