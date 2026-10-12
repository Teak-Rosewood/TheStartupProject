import pytest
from tsp.crawl.errors import UnsafeURL
from tsp.crawl.safe_url import safe_url


def resolver(mapping):
    async def resolve(host):
        return mapping[host]

    return resolve


PUBLIC = resolver({"example.com": ["93.184.216.34"], "v6.example.com": ["2606:2800:220:1::1"]})


async def test_public_host_is_pinned():
    r = await safe_url("https://example.com/a?b=1", resolver=PUBLIC)
    assert (r.scheme, r.host, r.port, r.ip) == ("https", "example.com", 443, "93.184.216.34")
    assert r.url == "https://example.com/a?b=1"


async def test_public_ipv6_ok():
    r = await safe_url("http://v6.example.com/", resolver=PUBLIC)
    assert r.ip == "2606:2800:220:1::1"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "//example.com/",
        "https://user:pw@example.com/",
        "https://example.com:8080/",
        "https://example.com:22/",
        "https:///nohost",
    ],
)
async def test_scheme_port_credentials_rejected(url):
    with pytest.raises(UnsafeURL):
        await safe_url(url, resolver=PUBLIC)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::]/",
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f000001/",  # hex
        "http://0177.0.0.1/",  # octal
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00:ec2::254]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://[64:ff9b::7f00:1]/",  # NAT64 wrapping 127.0.0.1
        "http://224.0.0.1/",
        "http://localhost/",
        "http://localhost./",
        "http://LOCALHOST/",
        "http://foo.localhost/",
    ],
)
async def test_private_and_loopback_rejected(url):
    with pytest.raises(UnsafeURL):
        await safe_url(url, resolver=resolver({}))


@pytest.mark.parametrize(
    "ips",
    [["10.1.2.3"], ["93.184.216.34", "169.254.169.254"], ["::1"], ["::ffff:192.168.0.1"]],
)
async def test_dns_answers_are_all_checked(ips):
    with pytest.raises(UnsafeURL):
        await safe_url("https://evil.test/", resolver=resolver({"evil.test": ips}))


async def test_dns_rebinding_second_lookup_is_not_trusted():
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    async def flip(host):
        return next(answers)

    first = await safe_url("https://rebind.test/", resolver=flip)
    assert first.ip == "93.184.216.34"  # callers connect to this pinned IP, never re-resolve
    with pytest.raises(UnsafeURL):
        await safe_url("https://rebind.test/", resolver=flip)


async def test_unresolvable_host_rejected():
    async def nxdomain(host):
        raise OSError("nxdomain")

    with pytest.raises(UnsafeURL):
        await safe_url("https://nope.test/", resolver=nxdomain)


async def test_extra_ports_only_when_configured():
    r = await safe_url("https://example.com:8443/", resolver=PUBLIC, allowed_ports=(80, 443, 8443))
    assert r.port == 8443
