vcl 4.1;

import cachetag;

backend default {
    .host = "127.0.0.1";
    .port = "8080";
}

sub vcl_init {
    new tags = cachetag.namespace("default");
}

sub vcl_deliver {
    set resp.http.X-Tag-Objects = tags.objects();
}
