import { useSearchParams } from 'react-router-dom';

export function usePreviewState() {
  const [params] = useSearchParams();
  const state = params.get('state');
  return ['loading', 'error', 'empty'].includes(state) ? state : null;
}
export default usePreviewState;
