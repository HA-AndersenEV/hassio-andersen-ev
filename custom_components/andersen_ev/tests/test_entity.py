"""Tests for the shared AndersenEvDeviceInfoMixin."""

from unittest.mock import MagicMock

from andersen_ev.entity import AndersenEvDeviceInfoMixin


class _StubEntity(AndersenEvDeviceInfoMixin):
    """Minimal stand-in exercising the mixin without a real CoordinatorEntity."""

    def __init__(self, device) -> None:
        self._device = device
        self._attr_device_info = {"serial_number": "test_device_123"}


def _make_device(last_status=None):
    device = MagicMock()
    device.last_status = last_status
    return device


class TestUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvDeviceInfoMixin._update_device_info_from_status()."""

    def test_known_product_id_maps_to_the_marketed_model_name(self):
        """The API's own product name is internal, so a confirmed id wins over it."""
        entity = _StubEntity(_make_device(last_status={"sysProductName": "Thurlestone", "sysProductId": 30}))

        entity._update_device_info_from_status()

        assert entity._attr_device_info["model"] == "A2"
        assert entity._attr_device_info["model_id"] == "30"

    def test_unknown_product_id_falls_back_to_the_product_name(self):
        """A charger we have never seen still gets a readable model, not a bare number."""
        entity = _StubEntity(_make_device(last_status={"sysProductName": "Newquay", "sysProductId": 99}))

        entity._update_device_info_from_status()

        assert entity._attr_device_info["model"] == "Newquay"
        assert entity._attr_device_info["model_id"] == "99"

    def test_product_name_without_a_product_id_leaves_model_id_unset(self):
        entity = _StubEntity(_make_device(last_status={"sysProductName": "Newquay"}))

        entity._update_device_info_from_status()

        assert entity._attr_device_info["model"] == "Newquay"
        assert "model_id" not in entity._attr_device_info

    def test_integer_product_id_rendered_as_string(self):
        entity = _StubEntity(_make_device(last_status={"sysProductId": 99}))

        entity._update_device_info_from_status()

        assert entity._attr_device_info["model"] == "99"
        assert entity._attr_device_info["model_id"] == "99"

    def test_hw_fw_and_serial_populated(self):
        entity = _StubEntity(
            _make_device(
                last_status={
                    "sysHwVersion": "4",
                    "sysFwVersion": "314",
                    "konnectSerial": "1234567890",
                }
            )
        )

        entity._update_device_info_from_status()

        assert entity._attr_device_info["hw_version"] == "4"
        assert entity._attr_device_info["sw_version"] == "314"
        assert entity._attr_device_info["serial_number"] == "1234567890"

    def test_null_fields_are_ignored(self):
        """Nullable GraphQL fields arrive as present-but-null, which must not become "None"."""
        entity = _StubEntity(
            _make_device(
                last_status={
                    "sysProductName": None,
                    "sysProductId": 99,
                    "sysHwVersion": None,
                    "sysFwVersion": None,
                    "konnectSerial": None,
                }
            )
        )

        entity._update_device_info_from_status()

        # A null product name must fall through to the product id rather than block it.
        assert entity._attr_device_info["model"] == "99"
        assert "hw_version" not in entity._attr_device_info
        assert "sw_version" not in entity._attr_device_info
        assert entity._attr_device_info["serial_number"] == "test_device_123"

    def test_blank_fields_are_ignored(self):
        entity = _StubEntity(
            _make_device(last_status={"sysProductName": "", "sysProductId": "", "konnectSerial": "   "})
        )

        entity._update_device_info_from_status()

        assert "model" not in entity._attr_device_info
        assert "model_id" not in entity._attr_device_info
        assert entity._attr_device_info["serial_number"] == "test_device_123"

    def test_missing_status_leaves_device_info_unchanged(self):
        entity = _StubEntity(_make_device(last_status=None))

        entity._update_device_info_from_status()

        assert entity._attr_device_info == {"serial_number": "test_device_123"}

    def test_empty_status_leaves_device_info_unchanged(self):
        entity = _StubEntity(_make_device(last_status={}))

        entity._update_device_info_from_status()

        assert entity._attr_device_info == {"serial_number": "test_device_123"}
