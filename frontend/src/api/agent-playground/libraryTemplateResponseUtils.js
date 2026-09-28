export function getLibraryTemplateItems(data) {
  return data?.result?.data ?? data?.result?.results ?? data?.results ?? [];
}

export function getLibraryTemplateTotalCount(data) {
  return data?.result?.total_count ?? data?.result?.count ?? data?.count ?? 0;
}

export function getNextLibraryTemplatePageParam(lastPage, allPages) {
  if (lastPage.data?.next || lastPage.data?.result?.next) {
    return allPages.length;
  }

  const totalCount = getLibraryTemplateTotalCount(lastPage.data);
  const fetchedCount = allPages.reduce(
    (count, page) => count + getLibraryTemplateItems(page.data).length,
    0,
  );

  if (fetchedCount < totalCount) return allPages.length;
  return undefined;
}
