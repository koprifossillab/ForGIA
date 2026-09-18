# 사내 CA 인증서

KOPRI 망은 TLS 를 가로채 자체 CA(`issuer=C=KR, O=KOPRI, CN=KOPRI SSL`)로 다시
서명한다. 호스트에는 이 인증서가 `/usr/local/share/ca-certificates/` 에 깔려 있지만
컨테이너 이미지에는 없어서, 빌드 중 `download.pytorch.org` 에서 이렇게 막힌다:

```
SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: self-signed certificate in certificate chain'))
```

확인해 보니 가로채는 대상은 `download.pytorch.org` 뿐이고 PyPI·GitHub·
huggingface.co 는 그대로 통과한다. 그래도 앞으로 대상이 늘 수 있으므로 CA 를
이미지에 넣어 둔다.

**여기 있는 것은 공개 인증서지 비밀이 아니다.** 다만 이 CA 를 신뢰한다는 것은
KOPRI 프록시가 그 이미지 안의 모든 TLS 를 들여다볼 수 있다는 뜻이다. 사내에서는
어차피 그렇지만, 망 밖에서 이 저장소를 쓴다면 **이 디렉토리를 비우면 된다** —
`Dockerfile.pipeline` 은 `.crt` 가 없으면 그냥 넘어간다.

호스트 원본: `/usr/local/share/ca-certificates/KOPRI_SSL_*.crt`, `kopri_ssl_root.crt`
