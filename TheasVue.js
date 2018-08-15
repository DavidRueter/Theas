function Theas() {
    this.useCurrentLocation = false;
    this.currentLocation = null;
    this.loadingCount = 0;
}


Theas.prototype.objToStr = function (obj, kstr, formObj) {
    var that =  this;

    var propdelim = ':';
    var nvdelim = '&';
    var ardelim = ',';

    var buf = '';

    if (!kstr) {
        kstr = '';
    }

    if (Array.isArray(obj)) {
        buf = obj.join(ardelim);
        if (formObj) {
            formObj.set(kstr, buf);
        }
        buf = kstr + '=' + buf;
    }
    else {
        for (var k in obj) {
            if (obj.hasOwnProperty(k)) {
                if (typeof obj[k] == 'object') {
                    buf = buf + (buf == '' ? '' : nvdelim ) + that.objToStr(obj[k], kstr + (kstr == '' ? '' : propdelim) + k, formObj);
                }
                else {
                    buf = buf + (buf == '' ? '' : nvdelim ) + kstr + (kstr == '' ? '' : propdelim) + k + '=' + obj[k];
                    if (formObj) {
                        formObj.set(kstr + (kstr == '' ? '' : propdelim) + k, obj[k]);
                    }
                }
            }
        }
    }
    return buf
};

Theas.prototype.submitAsync = function (v, config) {
    // save reference to Theas object
    var thatTheas = this;

    // save reference to Vue object that can be used in async callbacks
    var thatVue = v;

    // obtain current location
    if (typeof thatTheas.getCurrentLocation === 'function') {
        thatTheas.getCurrentLocation();
    }

    // access the HTML theasForm (that was rendered by the server)
    var theasForm = document.getElementById('theasForm');
    var theasFormData = new FormData(theasForm);

    if (thatTheas.currentLocation) {
        theasFormData.set('theas:th:currentLocation', JSON.stringify(thatTheas.currentLocation));
    }

    // add in values from theas object
    for (var k in thatVue.theas) {
        if (thatVue.theas.hasOwnProperty(k)) {
            theasFormData.set('theas:' + k.replace('$', ':'), thatVue.theas[k]);
        }
    }

    // add in data from config.data if applicable
    if (config && config.data) {
        this.objToStr(config.data, '', theasFormData);

      for (var n2 in config.data) {
          if (config.data.hasOwnProperty(n2)) {
              theasFormData.set(n2, config.data[n2]);
          }
      }
    }

    if (config && config.asyncCmd) {
        theasFormData.set('cmd', config.asyncCmd);
    }

    theasFormData.set('theas:lastFetch', config.lastFetchDate);

    // Set other form fields such as cmd
    if (thatTheas.currentLocation) {
        theasFormData.set('theas:th:currentLocation', JSON.stringify(thatTheas.currentLocation));
    }


    if (config.binaryFiles && config.binaryFiles.length > 0) {

        for (var i = 0; i < config.binaryFiles.length; i = i + 1) {
            var thisFile = config.binaryFiles[i];
            theasFormData.append(thisFile.fieldName, thisFile.binaryData, thisFile.fileName)
        }
    }

    thatTheas.loadingCount++;


    var axiosConfig = {};

    axiosConfig.method = 'post';
    axiosConfig.url = config.url;
    axiosConfig.data = theasFormData;

    if (config && config.binaryFiles && config.binaryFiles.length > 0) {
        axiosConfig.headers = {'Content-Type': 'multipart/form-data'};
    }
    else {
        axiosConfig.headers = {'Content-Type': 'application/x-www-form-urlencoded'};
    }

    axios(axiosConfig)
        .then(function (response) {
            //handle success

            var that = thatTheas;

            thatTheas.loadingCount--;

            if (response.data && response.data.length > 0 && response.data[0].JSONData) {
                config.responseTarget = thatTheas.merge(config.responseTarget, response.data[0].JSONData);
                config.responseDateTarget = response.data[0].FetchDate;
            }

            if (config.onResponse) {
                config.onResponse(response);
            }


            // Optionally, can navigate.

            if (config.onSuccessURL) {
                window.location = config.onSuccessURL;
            }

            /*
             // We could check TheasParams for theas:th:NextPage...but we aren't doing that right now
             // because we don't want to make assumptions about what the Async response data contained.

             var params;
             var nameValue;
             var thisName;
             var thisValue;
             var goToURL = '';

             if (response != undefined) {

             params = response.data.split('&');
             for (var i = 0; i < params.length; i++) {
             nameValue = params[i].split('=');

             thisName = nameValue[0];
             thisValue = nameValue[1];

             if (thisName == 'theas:th:NextPage') {
             // navigate to specified page
             goToURL = thisValue;
             }
             }

             if (!goToURL && config.onSuccessURL) {
             goToURL = config.onSuccessURL;
             }

             if (goToURL) {
             window.location = goToURL;
             }
             }
             */


        })
        .catch(function (response) {
            //handle error
            console.log(response);
        });


};

Theas.prototype.sortArray = function (ar, sortBy, descending) {
    // Utility function to sort an array of objects by the value of a property of the objects.

    // ar: contains an array to be sorted
    // sortBy:  contains a string indicating the property name to sort by
    // descending:  contains a boolean indicating whether sorting should be in descending order

    ar.sort(function (a, b) {
        var termA;
        var termB;

        if (a.hasOwnProperty(sortBy) && b.hasOwnProperty(sortBy)) {
            if (descending) {
                // intentionally swap A/B, because we want to sort descending
                termB = a[sortBy];
                termA = b[sortBy];
            }
            else {
                termA = a[sortBy];
                termB = b[sortBy];
            }
        }
        else {
            return 0
        }

        if (termA < termB) {
            return -1;
        }
        else if (termA > termB) {
            return 1;
        }
        else {
            return 0;
        }
    });

    return ar
};

Theas.prototype.merge = function () {
    // Utility function to take two or more arrays of objects...each of which that contains a
    // qguid property...and return a new array that contains a list of all unique objects
    // from all arrays.


    // Pass in two or more arrays of objects that contain a uniqueKey property (defaults to 'qguid').
    // Returns a new array that contains a list of all unique objects from all arrays.

    // If the first argument is a string, it is taken to indicate the key containing a unique value that is
    // present in all of the arrays being passed in.  If the first argument is not a string, the uniqueKey
    // key name will be 'qguid'.

    var that = this;

    var ar = [],
        a,
        al = arguments.length,
        firstArParam = 0,
        key,
        thisObj,
        uniqueKey = 'qguid',
        excludeId;


    for (a = 0; a < al; a++) {
        var thisParamType = typeof arguments[a];
        if ( thisParamType === 'string') {
            switch (a) {
                case 0: {
                    // first parameter is a string.  Take that to be the name of the uniqueKey by which we are to merge.
                    uniqueKey = arguments[a];
                    firstArParam++;
                    break;
                }

                case 1: {
                    // second parameter is a string.  Take that to be the ID value that we want to exclude form merge.
                    excludeId = arguments[a];
                    firstArParam++;
                    break;
                }
            }

        }
        else {
            break;
        }

    }

    // loop through all arguments
    for (a = firstArParam; a < al; a++){
        for (key in arguments[a]) {
            if (arguments[a].hasOwnProperty(key)) {
                thisObj = arguments[a][key];
                if (thisObj.hasOwnProperty(uniqueKey)) {
                    // Note:  object will be omitted if there is no uniqueKey property

                    if (a == firstArParam || thisObj[uniqueKey] != excludeId) {
                        // this is the first array, or we are not supposed to exclude this id
                        ar[thisObj[uniqueKey]] = thisObj;
                    }
                }
            }
        }
    }

    // Note that ar is an associative array with a key of qguid,
    // whereas the arguments had a simple javascript Array ("real" array) with a sequential
    // integer index for the key.  Need to return a "real" array.

    var ar2 = [];

    for (key in ar) {
        if (ar.hasOwnProperty(key)) {
            ar2.push(ar[key]);
        }
    }

    //this.sortArray(ar2);

    return ar2;
};

Theas.prototype.arrayObjToNV = function (aa, nameKey, textKey, nameOutKey, textOutKey, sortBy, descending) {
    // Utility function to take an array of objects and to use it to create a simple name/value array

    // Given any array of arbitrary objects, create a plain array of name-value pairs

    // Some things, notably bootstrap-vue :options attributes for b-form-select components, require
    // a simple javascript array with keys of 'value' and 'text'.

    // Usually JSON data will contain arrays of objects.  This function makes it simple to translate
    // into the needed array.

    //aa:  array of arbitrary objects
    //nameKey:  key of objects in aa to be used for the "name" of the name/value pair
    //textKey:  key of objects in aa to be used for the "value" of the name/value pair
    //nameOutKey:  Optional.  Key to be used in the output array for the "name"  (defaults to "value" for use in :options)
    //textOutKey:  Optional.  Key to be used in the output array for the "value" (defaults to "text" for use in :options)
    //sortBy:  Optional.  If provided, contains a key name in the output array to sort by
    //descending:  Optional.  If sortBy is provided, descending contains a boolean to indicate if sort should be in descending order

    var thatTheas = this;

    var ap = [];

    if (!nameOutKey) {
        nameOutKey = 'value';
    }

    if (!textOutKey) {
        textOutKey = 'text';
    }

    for (var thiskey in aa) {
        if (aa.hasOwnProperty(thiskey)) {
            var obj = {};
            obj[nameOutKey] = aa[thiskey][nameKey];
            obj[textOutKey] = aa[thiskey][textKey];
            ap.push(obj);
        }
    }
    if (sortBy) {
        thatTheas.sortArray(ap, sortBy, descending);
    }

    return ap;
};

Theas.prototype.submitForm = function (v, config) {
    // theasForm contains the HTML theasForm (that was rendered by the server)    
    var theasForm = document.getElementById('theasForm');
    var vueObj = v;

    if (vueObj.submitted) {
        return;
    }

    // tell Theas server that we want to update data
    vueObj.theas.th$PerformUpdate = '1';


    var theasFormData = {};

    /*
     We want to retrieve the form field values from theasForm and incorporate them into the data we
     will be submitting via Axios.  This should be easy to do:

     var theasFormData = new FormData(theasForm);
     theasFormData.append('someField'. 'someValue');
     theasFormData.set('anotherField', 'anotherValue');

     But Apple / Safari / iOS does not fully support FormData()j or URLSearchParams() , so this does
     not work.

     Instead, we will do this ugly walk through the DOM.
     */

    var i, j;
    for (i = theasForm.elements.length - 1; i >= 0; i = i - 1) {
        if (theasForm.elements[i].name) {
            switch (theasForm.elements[i].nodeName) {
                case 'INPUT':
                    switch (theasForm.elements[i].type) {
                        case 'text':
                        case 'hidden':
                        case 'password':
                        case 'button':
                        case 'reset':
                        case 'submit':
                            theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            break;
                        case 'checkbox':
                        case 'radio':
                            if (theasForm.elements[i].checked) {
                                theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            }
                            break;
                        case 'file':
                            break;
                    }
                    break;
                case 'TEXTAREA':
                    theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                    break;
                case 'SELECT':
                    switch (theasForm.elements[i].type) {
                        case 'select-one':
                            theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            break;
                        case 'select-multiple':
                            for (j = theasForm.elements[i].options.length - 1; j >= 0; j = j - 1) {
                                if (theasForm.elements[i].options[j].selected) {
                                    theasFormData[theasForm.elements[i].name] = theasForm.elements[i].options[j].value;
                                    //to do--not quite right.  Need support for multiple select.
                                }
                            }
                            break;
                    }
                    break;
                case 'BUTTON':
                    switch (theasForm.elements[i].type) {
                        case 'reset':
                        case 'submit':
                        case 'button':
                            theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            break;
                    }
                    break;
            }

        }
    }

    for (var k in vueObj.theas) {
        if (vueObj.theas.hasOwnProperty(k)) {
            theasFormData['theas:' + k.replace('$', ':')] = vueObj.theas[k];
        }
    }


    // add in data from config.data if applicable
    if (config && config.data) {
        for (var n2 in config.data) {
            if (config.data.hasOwnProperty(n2)) {
                theasFormData[n2] = config.data[n2];
            }
        }
    }


    if (config && config.asyncCmd) {
        theasFormData['cmd'] = config.asyncCmd;
    }


    var buf = null;

    if (config.binaryFiles && config.binaryFiles.length > 0) {

        // we must use FormData
        buf = new FormData();

        for (var n3 in theasFormData) {
            if (theasFormData.hasOwnProperty(n3)) {
                buf.append(n3, theasFormData[n3]);
            }
        }

        for (i = 0; i < config.binaryFiles.length; i = i + 1) {
            var thisFile = config.binaryFiles[i];
            buf.append(thisFile.fieldName, thisFile.binaryData[0], thisFile.fileName)
        }

    }
    else {

        // we can use a string
        buf = '';

        if (theasFormData) {
            for (var n in theasFormData) {
                if (theasFormData.hasOwnProperty(n)) {
                    buf = buf + n + '=' + encodeURIComponent(theasFormData[n]) + '&';
                }
            }
        }
    }


    var axiosConfig = {};

    axiosConfig.method = 'post';
    axiosConfig.url = config.url;
    axiosConfig.data = buf;

    if (config && config.binaryFiles && config.binaryFiles.length > 0) {
        axiosConfig.headers = {'Content-Type': 'multipart/form-data'};
    }

    axios(axiosConfig)
        .then(function (response) {
            //handle success
            console.log(response);

            var params;
            var nameValue;
            var thisName;
            var thisValue;

            var goToURL = '';

            if (response != undefined) {
                params = response.data.split('&');
                for (var i = 0; i < params.length; i++) {
                    nameValue = params[i].split('=');

                    thisName = nameValue[0];
                    thisValue = nameValue[1];

                    if (thisName == 'theas:th:NextPage') {
                        // navigate to specified page
                        goToURL = thisValue;
                    }
                }

                if (!goToURL && config.onSuccessURL) {
                    goToURL = config.onSuccessURL
                }

                if (goToURL) {
                    window.location = goToURL;
                }
            }

        })
        .catch(function (response) {
            //handle error
            console.log(response);
        });

    vueObj.submitted = true;

};

Theas.prototype.getCurrentLocation = function () {
    var that = this;

    if (that.useCurrentLocation) {

        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                function (position) {
                    if (position) {
                        that.currentLocation = {};
                        that.currentLocation['lat'] = position.coords.latitude;
                        that.currentLocation['long'] = position.coords.longitude;
                    }
                },
                function () {
                    that.currentLocation = null
                });
        }
        else {
            that.currentLocation = null;
        }
    }
};