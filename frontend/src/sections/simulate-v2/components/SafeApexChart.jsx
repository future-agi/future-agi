import ReactApexChart from "react-apexcharts";

/*
  react-apexcharts discards the render() promise and calls destroy()/update
  unconditionally. When a chart's render rejects, ApexCharts never creates its
  SVG paper, and the later destroy() throws "reading 'node'" during unmount —
  which takes down the whole page on a tab switch.
*/
const hasPaper = (chart) => !!chart?.w?.globals?.dom?.Paper;

export default class SafeApexChart extends ReactApexChart {
  componentDidMount() {
    this.chart = new window.ApexCharts(this.chartRef.current, this.getConfig());
    this.chart.render().catch((err) => {
      console.error(`[SafeApexChart] ${this.props.type} chart failed to render`, err, this.props);
    });
  }

  componentDidUpdate(prevProps) {
    if (!hasPaper(this.chart)) return;
    super.componentDidUpdate(prevProps);
  }

  componentWillUnmount() {
    if (!this.chart) return;
    if (hasPaper(this.chart)) {
      this.chart.destroy();
    } else {
      window.removeEventListener("resize", this.chart.windowResizeHandler);
    }
  }
}
