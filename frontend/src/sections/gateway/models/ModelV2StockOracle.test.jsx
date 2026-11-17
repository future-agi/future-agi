import stock from "./test-fixtures/stock-catalog.json";
import { defineV2OracleSuite } from "./test-fixtures/v2-stock-oracle-suite";

defineV2OracleSuite("stock", stock);
