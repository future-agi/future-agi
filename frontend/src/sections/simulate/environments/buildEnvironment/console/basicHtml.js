// A bare text-formatting tag, no attributes: the only raw HTML `basicHtml` renders.
export const isBasicHtmlTag = (value) =>
  /^<\/?(b|strong|i|em|s|del|strike|ins|sub|sup|kbd|code)>$|^<br\s*\/?>$/i.test(
    value.trim(),
  );
